#!/bin/sh
# SPDX-License-Identifier: GPL-2.0
#
# Capture a function trace without treating "not observed" as proof of dead
# code. For early boot use the kernel command line:
#   ftrace=function trace_buf_size=64M initcall_debug
# and call this script with --snapshot immediately after userspace starts.

set -eu

tracefs=${TRACEFS:-/sys/kernel/tracing}
output=
duration=10
filter_file=
snapshot=0

usage()
{
    echo "usage: $0 --output FILE [--duration SEC] [--filter FILE] [--snapshot] [-- command ...]" >&2
    exit 2
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --output)
            [ "$#" -ge 2 ] || usage
            output=$2
            shift 2
            ;;
        --duration)
            [ "$#" -ge 2 ] || usage
            duration=$2
            shift 2
            ;;
        --filter)
            [ "$#" -ge 2 ] || usage
            filter_file=$2
            shift 2
            ;;
        --snapshot)
            snapshot=1
            shift
            ;;
        --)
            shift
            break
            ;;
        *)
            usage
            ;;
    esac
done

[ -n "$output" ] || usage
[ -r "$tracefs/trace" ] || {
    echo "tracefs is unavailable at $tracefs" >&2
    exit 1
}

if [ "$snapshot" -eq 0 ]; then
    [ -w "$tracefs/current_tracer" ] || {
        echo "tracefs is not writable; run as root" >&2
        exit 1
    }
    echo 0 > "$tracefs/tracing_on"
    echo nop > "$tracefs/current_tracer"
    : > "$tracefs/trace"
    if [ -n "$filter_file" ]; then
        [ -r "$filter_file" ] || {
            echo "cannot read filter: $filter_file" >&2
            exit 1
        }
        cp "$filter_file" "$tracefs/set_ftrace_filter"
    else
        : > "$tracefs/set_ftrace_filter"
    fi
    echo function > "$tracefs/current_tracer"
    echo 1 > "$tracefs/tracing_on"
    if [ "$#" -gt 0 ]; then
        "$@"
    else
        sleep "$duration"
    fi
    echo 0 > "$tracefs/tracing_on"
fi

cp "$tracefs/trace" "$output"
echo "ftrace snapshot written to $output"
