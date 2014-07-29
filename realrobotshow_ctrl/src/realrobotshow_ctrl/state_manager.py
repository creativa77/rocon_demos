#!/usr/bin/env python
#
# License: BSD
#   https://raw.github.com/robotics-in-concert/rocon_demos/license/LICENSE
#
import rospy
import simple_delivery_msgs.msg as simple_delivery_msgs
import yocs_msgs.msg as yocs_msgs
import kobuki_msgs.msg as kobuki_msgs
import std_msgs.msg as std_msgs
import actionlib
import copy

from .utils import *


STATE_INITIALIZATION = 'INITIALIZATION'
STATE_GOTO_KITCHEN   = 'GOTO_KITCHEN'
STATE_AT_KITCHEN     = 'AT_KITCHEN'
STATE_GOTO_TABLE     = 'GOTO_TABLE'
STATE_AT_TABLE       = 'AT_TABLE'
STATE_ON_ERROR       = 'ON_ERROR'

# INIT
# GOTO KITCHEN
# AT_KITCHEN
# GOTO_TABLE
# AT_TABLE
# ERROR

class StateManager(object):
    
    _kitchen_action_name = 'deliver_order'
    _navigator_action_name = 'navigate_to'
    _kitchen_location = 'kitchen'

    _confirm_sound = 'kaku.wav'
    _retry_sound = 'moo.wav'
    _navi_failed_sound = 'angry_cat.wav'
    _order_received_sound = 'kaku.wav'
    _at_table_sound = 'lion.wav'
    _enjoy_meal_sound = 'meow.wav'
    _bab_sound = 'pab.wav'

    def __init__(self):
        self._init_variables()
        self._init_handles()
        self._init_states()
        self._init_led_blinkder()

    def _init_states(self):
        self._states = {}
        self._states[STATE_INITIALIZATION] = self._state_initialization
        self._states[STATE_GOTO_KITCHEN] = self._state_goto_kitchen
        self._states[STATE_AT_KITCHEN] = self._state_at_kitchen
        self._states[STATE_GOTO_TABLE] = self._state_goto_table
        self._states[STATE_AT_TABLE]   = self._state_at_table
        self._states[STATE_ON_ERROR]      = self._state_on_error

    def _init_variables(self):
        self._current_state = STATE_ON_ERROR
        self._initialized = False
        self._previous_button = None  
        self._init_requested = False
        self._delivery_order_received = False
        self._customer_confirm = False
        self._order_in_progress = False

        self._nav_kitchen_timeout = rospy.get_param('~nav_kitchen_timeout', 300.0)
        self._nav_table_timeout = rospy.get_param('~nav_table_timeout', 300.0)
        self._nav_retry         = rospy.get_param('~nav_retry', 3)
        self._nav_table_distance = rospy.get_param('~nav_table_distance', 5.0)
        self._resource_path = rospy.get_param('~resource_path')

    def _init_led_blinkder(self):
        self._red_led = kobuki_msgs.Led.RED
        self._off_led = kobuki_msgs.Led.BLACK
        self._green_led = kobuki_msgs.Led.GREEN
        self._orange_led = kobuki_msgs.Led.ORANGE
        self.last_blink_led = 2

    def _init_handles(self):
        # order handle
        self._deliver_order_handler = actionlib.SimpleActionServer(self._kitchen_action_name, simple_delivery_msgs.DeliverOrderAction, auto_start=False)
        self._deliver_order_handler.register_goal_callback(self._process_deliver_order)
        self._deliver_order_handler.register_preempt_callback(self._process_deliver_order_preempt)

        self._sub = {}
        self._pub = {}

        # Debug        
        self._pub['debug'] = rospy.Publisher('robot_status', std_msgs.String, queue_size=2)
        # Button
        self._sub['digital_inputs'] = rospy.Subscriber('~digital_inputs', kobuki_msgs.DigitalInputEvent, self._process_button)  # Waiterbot buttons.

        # Localize manager
        self._pub['localize'] = rospy.Publisher('~localize', std_msgs.Empty, queue_size=1)
        self._sub['localized'] = rospy.Subscriber('~localized', std_msgs.Empty, self._process_localized)

        # semantic navigation handler
        self._navigator_handler = actionlib.SimpleActionClient(self._navigator_action_name, yocs_msgs.NavigateToAction)

        self.loginfo('Wait for Sematic Navigator Server to be up')
        self._navigator_handler.wait_for_server()

        # LED
        self._pub['led1'] = rospy.Publisher('/mobile_base/commands/led1', kobuki_msgs.Led, queue_size=1)
        self._pub['led2'] = rospy.Publisher('/mobile_base/commands/led2', kobuki_msgs.Led, queue_size=1)

    def _process_button(self, msg):
        # 0 = GREEN 1 = RED

        if not self._previous_button:
            self._previous_button = copy.deepcopy(msg)
            return

        green, red = check_button_event(self._previous_button, msg)

        if green:
            if not self._current_state:
                self._current_state = STATE_INITIALIZATION
        
            if self._current_state == STATE_ON_ERROR:
                self._current_state = STATE_INITIALIZATION

            if self._current_state == STATE_AT_TABLE:
                self._customer_confirm = True

        self._previous_button = copy.deepcopy(msg)

    def _process_localized(self, msg): 
        self._initialized = True
        
    def _process_deliver_order(self):
        self.loginfo('Received Goal ')
        r = simple_delivery_msgs.DeliverOrderResult()

        if self._current_state != STATE_AT_KITCHEN:
            message = 'Robot is not at kitchen. Ignore the order!'
            self.logwarn(message)
            goal = self._deliver_order_handler.accept_new_goal()
            r = simple_delivery_msgs.DeliverOrderResult()
            r.message = message
            r.success = False
            self._deliver_order_handler.set_succeeded(r)
        else:
            goal = self._deliver_order_handler.accept_new_goal()
            self.loginfo(str(goal))
            self._delivery_location = goal.location
            self._delivery_order_received = True

    def _process_deliver_order_preempt(self):
        pass

    def spin(self):
        r = rospy.Rate(10)
        self._deliver_order_handler.start()

        t = 2

        while not rospy.is_shutdown():
            self._states[self._current_state]()
            t = (t % 5) + 1

            if t == 1:
                self._logging()
                self._blink_leds()
            r.sleep()

    def _logging(self):
        #self.loginfo(self._current_state)
        self._pub['debug'].publish(str(self._current_state))

        if self._order_in_progress:
            feedback = yocs_msgs.NavigateToFeedback()
            feedback.status = str("Status : " + self._current_state + "  [" + str(self._navigator_feed) + "]")
            self._deliver_order_handler.publish_feedback(feedback)
                
    def loginfo(self, msg):
        rospy.loginfo('Robot State Manager : ' + str(msg))

    def logwarn(self, msg):
        rospy.logwarn('Robot State Manager : ' + str(msg))

    def _request_navigator(self, location, approach_type, num_retry, timeout, distance):
        goal = yocs_msgs.NavigateToGoal()

        goal.location = location
        goal.approach_type = approach_type
        goal.num_retry = num_retry
        goal.timeout = float(timeout)
        goal.distance = distance

        self.loginfo(str(goal))

        self._navigator_handler.send_goal(goal, done_cb=self._navigator_done, feedback_cb=self._navigator_feedback)
        self._navigator_finished = False 

    def _navigator_done(self, status, result):
        self.loginfo("Navigator Result : %s, Message : %s"%(result.success,result.message)) 
        
        if result.success == False:
            play_sound(self._resource_path, self._navi_failed_sound)
            self._current_state = STATE_ON_ERROR
        self._navigator_finished= True
        
    def _navigator_feedback(self, feedback):
        self._navigator_feed = str("Distance : %s, Message : %s"%(str(feedback.distance),str(feedback.message)))
        self.loginfo("Navigator : " + str(self._navigator_feed))
        
        if feedback.status == yocs_msgs.NavigateToFeedback.STATUS_RETRY:
            play_sound(self._resource_path, self._retry_sound)


    def _state_initialization(self):
        if not self._init_requested:
            self._initialized = False
            self._pub['localize'].publish() 
            self.loginfo('Localization Request sent')
            self._init_requested = True
            play_sound(self._resource_path, self._confirm_sound)
    
        if self._initialized:
            self.loginfo('Robot Localized')
            self.loginfo('Moving To kitchen')

            self._request_navigator('kitchen', yocs_msgs.NavigateToGoal.APPROACH_ON, self._nav_retry, self._nav_kitchen_timeout, 0.0)
            # Request navigator to go kitchen
            self._current_state = STATE_GOTO_KITCHEN
            self._init_requested = False

    def _state_goto_kitchen(self):
        # Wait for arriving
        if self._navigator_finished:
            # When it arrives...
            self._current_state = STATE_AT_KITCHEN
            if self._order_in_progress:
                self._order_in_progress = False
                message = 'Delivery Success!'
                r = simple_delivery_msgs.DeliverOrderResult()
                r.message = message
                r.success = False
                self._deliver_order_handler.set_succeeded(r)

            play_sound(self._resource_path, self._bab_sound)

    def _state_at_kitchen(self):
        # Wait for order
        if self._delivery_order_received:
            self._delivery_order_received = False
            self._order_in_progress = True
            self._request_navigator(self._delivery_location, yocs_msgs.NavigateToGoal.APPROACH_ON, self._nav_retry, self._nav_table_timeout, self._nav_table_distance)
            self._current_state = STATE_GOTO_TABLE
            # Make a sound
            play_sound(self._resource_path, self._confirm_sound)

    def _state_goto_table(self):
        # Wait for arriving
        if self._navigator_finished:
            # When it arrives...
            self._current_state = STATE_AT_TABLE
            play_sound(self._resource_path, self._at_table_sound)
            # arriving sound

    def _state_at_table(self):
        # Wait for Customer's confirmation
        if self._customer_confirm:
            self._customer_confirm = False
            self.loginfo('Moving To kitchen')                                                     
            play_sound(self._resource_path, self._enjoy_meal_sound)
            self._request_navigator('kitchen', yocs_msgs.NavigateToGoal.APPROACH_ON, 3, 300, 0.0)
            # Request navigator to go kitchen
            self._current_state = STATE_GOTO_KITCHEN

    def _state_on_error(self):
        # When it fails while navigation....
        pass

    def _blink_leds(self):
        self.last_blink_led = (self.last_blink_led % 2) + 1

        if self._current_state == STATE_ON_ERROR:
            led1_blink = self._off_led if self.last_blink_led == 1 else self._red_led
            led2_blink = self._red_led if self.last_blink_led == 1 else self._off_led
            self._pub['led1'].publish(led1_blink)
            self._pub['led2'].publish(led2_blink)
        else:
            led1_blink = self._off_led if self.last_blink_led == 1 else self._green_led 
            led2_blink = self._green_led if self.last_blink_led == 1 else self._off_led
            self._pub['led1'].publish(led1_blink)
            self._pub['led2'].publish(led2_blink)
