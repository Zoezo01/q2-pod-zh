#!/bin/sh
# S12: the GPIO driver has loaded, but the player has not read its saved DAC charging choice.
case "$1" in
    start) /usr/bin/cmd_gpio set_func PE22 output1 ;;
esac
