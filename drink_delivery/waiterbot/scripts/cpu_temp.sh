#!/bin/bash

while :
do
  sensors | grep Physical
  sensors | grep Physical > cpu_tmp.txt
  sleep 1
done