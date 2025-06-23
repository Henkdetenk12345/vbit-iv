#!/usr/bin/env python3

# T42 Teletext Stream to In-vision decoder
#
# Copyright (c) 2020-2021 Peter Kwan
# Enhanced by Max de Vos 2025
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
print('VBIT-iv System started')

import sys
import time
from ttxpage import TTXpage
import zmq
from packet import Packet, metaData
from clut import clut, Clut

# Globals
packetSize = 42
head = 0
tail = 0

currentMag = 1
currentPage = 0x00
capturing = False
wasCapturing = False
elideRow = 0
seeking = True
lastPacket = b"AB0123456789012345678901234567890123456789"
holdMode = False
subCode = 0
lastSubcode = 0
rowCounter = 0
suppressHeader = False
pageNum = "100"

# Nieuw: bijhouden van actieve subpagina's
subpage_seen = set()

ttx = TTXpage()

print(sys.argv)
if int(sys.argv[1]) > 0:
    currentMag = int(sys.argv[1]) % 8
print("mag = " + str(currentMag))
if int(sys.argv[2]) > 0:
    currentPage = int(sys.argv[2], 16)
print("page = " + str(currentPage))

def deham(value):
    b0 = (value & 0x02) >> 1
    b1 = (value & 0x08) >> 2
    b2 = (value & 0x20) >> 3
    b3 = (value & 0x80) >> 4
    return b0 + b1 + b2 + b3

def mrag(v1, v2):
    rowlsb = deham(v1)
    mag = rowlsb % 8
    if mag == 0:
        mag = 8
    row = deham(v2) << 1
    if (rowlsb & 0x08) > 0:
        row = row + 1
    return mag, row

def decodePage(pkt):
    tens = deham(pkt[3])
    units = deham(pkt[2])
    return tens * 0x10 + units

def decodeSubcode(pkt):
    s1 = deham(pkt[4])
    s2 = deham(pkt[5]) & 0x07
    s3 = deham(pkt[6])
    s4 = deham(pkt[7]) & 0x03
    return (s4 << 11) + (s3 << 7) + (s2 << 4) + s1

def getC7(pkt):
    s1 = deham(pkt[8])
    return s1 & 0x01

def remote(ch):
    global pageNum, currentMag, currentPage, lastPacket, seeking, holdMode
    if ch == '':
        return
    if ch == 'h':
        holdMode = not holdMode
        return
    if ch == 'r':
        ttx.toggleReveal()
        return
    if ch == 'q' or ord(ch) == 27:
        exit()
    if ch in ('P', 'u', 'Q', 'i', 'R', 'o', 'S', 'p'):
        index = {'P': 0, 'u': 0, 'Q': 1, 'i': 1, 'R': 2, 'o': 2, 'S': 3, 'p': 3}[ch]
        currentMag = ttx.getMag(index)
        currentPage = ttx.getPage(index)
        seeking = True
        ttx.clear()
        return
    if ch >= '0' and ch <= '9':
        pageNum = pageNum + ch
        pageNum = pageNum[1:4]
        if pageNum[0] > '0' and pageNum[0] < '9':
            currentMag = int(pageNum[0])
            currentPage = int(pageNum[1:3], 16)
            seeking = True
        ttx.printHeader(lastPacket, 'P' + pageNum + '    ', seeking, False)

def process(pkt):
    global capturing, wasCapturing, currentMag, currentPage, elideRow, rowCounter
    global lastPacket, seeking, holdMode, subCode, lastSubcode, suppressHeader
    global subpage_seen

    if len(pkt) < 42:
        print("invalid teletext packet")
        exit()

    mag, row = mrag(pkt[0], pkt[1])

    if currentMag == mag:
        if row == 0:
            elideRow = 0
            if holdMode:
                ttx.printHeader(lastPacket, "HOLD    ", False, False)
                return
            page = decodePage(pkt)
            subcode = decodeSubcode(pkt)
            capturing = currentPage == page
            if capturing:
                rowCounter = 0
                seeking = False
                lastPacket = pkt
                suppressHeader = getC7(pkt) > 0
                if subcode != lastSubcode:
                    lastSubcode = subcode
                    ttx.clear()
                    subpage_seen.clear()
                wasCapturing = True
            else:
                if wasCapturing:
                    wasCapturing = False
                    print("[vbit-iv::process] Page load completed. rowCounter = " + str(rowCounter))
                    for i in range(1, 25):
                        if i not in subpage_seen:
                            ttx.printRow(b" " * 42, i)
                    subpage_seen.clear()
            if seeking:
                suppressHeader = False
                clut.reset()
            ttx.printHeader(pkt, "P{:1d}{:02X}    ".format(currentMag, currentPage), seeking, suppressHeader)
        else:
            if elideRow > 0 and elideRow == row:
                ttx.mainLoop()
                elideRow = 0
                return
            if capturing:
                subpage_seen.add(row)
                if row < 25:
                    if ttx.printRow(pkt, row):
                        elideRow = row + 1
                    rowCounter += 1
                if row in (26, 28):
                    metaData.decode(pkt, row)
                if row == 27:
                    ttx.decodeLinks(pkt)

    ttx.mainLoop()

# Start
bind = "tcp://*:7777"
print("vbit-iv binding to " + bind)
context = zmq.Context()
socket = context.socket(zmq.REP)
socket.bind(bind)

try:
    while True:
        for line in range(16):
            packet = sys.stdin.buffer.read(packetSize)
            if len(packet) < 42:
                print("No source data.")
            else:
                process(packet)
        key = ttx.getKey()
        if key != ' ':
            if key == 'q' or key == 27:
                exit()
            remote(key)
        time.sleep(0.020)
        try:
            message = socket.recv(flags=zmq.NOBLOCK).decode("utf-8")
            remote(message)
            socket.send(b"sup?")
        except zmq.Again:
            time.sleep(0.001)
except KeyboardInterrupt:
    print("Keyboard interrupt")
finally:
    print("vbit-iv clean up")
