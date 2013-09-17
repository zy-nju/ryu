# Copyright (C) 2012 Nippon Telegraph and Telephone Corporation.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import abc
import struct
import socket
from . import packet_base
from . import packet_utils
from . import icmpv6
from . import tcp
from ryu.ofproto import inet
from ryu.lib import addrconv
from ryu.lib import stringify


IPV6_ADDRESS_PACK_STR = '!16s'
IPV6_ADDRESS_LEN = struct.calcsize(IPV6_ADDRESS_PACK_STR)
IPV6_PSEUDO_HEADER_PACK_STR = '!16s16s3xB'


class ipv6(packet_base.PacketBase):
    """IPv6 (RFC 2460) header encoder/decoder class.

    An instance has the following attributes at least.
    Most of them are same to the on-wire counterparts but in host byte order.
    IPv6 addresses are represented as a string like 'ff02::1'.
    __init__ takes the correspondig args in this order.

    .. tabularcolumns:: |l|p{30em}|l|

    ============== ======================================== ==================
    Attribute      Description                              Example
    ============== ======================================== ==================
    version        Version
    traffic_class  Traffic Class
    flow_label     When decoding, Flow Label.
                   When encoding, the most significant 8
                   bits of Flow Label.
    payload_length Payload Length
    nxt            Next Header
    hop_limit      Hop Limit
    src            Source Address                           'ff02::1'
    dst            Destination Address                      '::'
    ext_hdrs       Extension Headers
    ============== ======================================== ==================
    """

    _PACK_STR = '!IHBB16s16s'
    _MIN_LEN = struct.calcsize(_PACK_STR)
    _IPV6_EXT_HEADER_TYPE = {}

    @staticmethod
    def register_header_type(type_):
        def _register_header_type(cls):
            ipv6._IPV6_EXT_HEADER_TYPE[type_] = cls
            return cls
        return _register_header_type

    def __init__(self, version, traffic_class, flow_label, payload_length,
                 nxt, hop_limit, src, dst, ext_hdrs=[]):
        super(ipv6, self).__init__()
        self.version = version
        self.traffic_class = traffic_class
        self.flow_label = flow_label
        self.payload_length = payload_length
        self.nxt = nxt
        self.hop_limit = hop_limit
        self.src = src
        self.dst = dst
        if ext_hdrs:
            assert isinstance(ext_hdrs, list)
            last_hdr = None
            for ext_hdr in ext_hdrs:
                assert isinstance(ext_hdr, header)
                if last_hdr:
                    ext_hdr.set_nxt(last_hdr.nxt)
                    last_hdr.nxt = ext_hdr.TYPE
                else:
                    ext_hdr.set_nxt(self.nxt)
                    self.nxt = ext_hdr.TYPE
                last_hdr = ext_hdr
        self.ext_hdrs = ext_hdrs

    @classmethod
    def parser(cls, buf):
        (v_tc_flow, payload_length, nxt, hlim, src, dst) = struct.unpack_from(
            cls._PACK_STR, buf)
        version = v_tc_flow >> 28
        traffic_class = (v_tc_flow >> 20) & 0xff
        flow_label = v_tc_flow & 0xfffff
        hop_limit = hlim
        offset = cls._MIN_LEN
        last = nxt
        ext_hdrs = []
        while True:
            cls_ = cls._IPV6_EXT_HEADER_TYPE.get(last)
            if not cls_:
                break
            hdr = cls_.parser(buf[offset:])
            ext_hdrs.append(hdr)
            offset += len(hdr)
            last = hdr.nxt
        # call ipv6.__init__() using 'nxt' of the last extension
        # header that points the next protocol.
        msg = cls(version, traffic_class, flow_label, payload_length,
                  last, hop_limit, addrconv.ipv6.bin_to_text(src),
                  addrconv.ipv6.bin_to_text(dst), ext_hdrs)
        return (msg, ipv6.get_packet_type(last),
                buf[offset:offset+payload_length])

    def serialize(self, payload, prev):
        hdr = bytearray(40)
        v_tc_flow = (self.version << 28 | self.traffic_class << 20 |
                     self.flow_label << 12)
        struct.pack_into(ipv6._PACK_STR, hdr, 0, v_tc_flow,
                         self.payload_length, self.nxt, self.hop_limit,
                         addrconv.ipv6.text_to_bin(self.src),
                         addrconv.ipv6.text_to_bin(self.dst))
        if self.ext_hdrs:
            for ext_hdr in self.ext_hdrs:
                hdr.extend(ext_hdr.serialize())
        return hdr

    def __len__(self):
        ext_hdrs_len = 0
        for ext_hdr in self.ext_hdrs:
            ext_hdrs_len += len(ext_hdr)
        return self._MIN_LEN + ext_hdrs_len

ipv6.register_packet_type(icmpv6.icmpv6, inet.IPPROTO_ICMPV6)
ipv6.register_packet_type(tcp.tcp, inet.IPPROTO_TCP)


class header(stringify.StringifyMixin):
    """extension header abstract class."""

    __metaclass__ = abc.ABCMeta

    def __init__(self):
        self.nxt = None

    def set_nxt(self, nxt):
        self.nxt = nxt

    @classmethod
    @abc.abstractmethod
    def parser(cls, buf):
        pass

    @abc.abstractmethod
    def serialize(self):
        pass

    @abc.abstractmethod
    def __len__(self):
        pass

# TODO: implement a class for routing header


class opt_header(header):
    """an abstract class for Hop-by-Hop Options header and destination
    header."""

    _PACK_STR = '!BB'
    _MIN_LEN = struct.calcsize(_PACK_STR)
    _FIX_SIZE = 8

    @abc.abstractmethod
    def __init__(self, size, data):
        super(opt_header, self).__init__()
        assert not (size % 8)
        self.size = size
        self.data = data

    @classmethod
    def parser(cls, buf):
        (nxt, len_) = struct.unpack_from(cls._PACK_STR, buf)
        data_len = cls._FIX_SIZE + int(len_)
        data = []
        size = cls._MIN_LEN
        while size < data_len:
            (type_, ) = struct.unpack_from('!B', buf[size:])
            if type_ == 0:
                opt = option(type_, -1, None)
                size += 1
            else:
                opt = option.parser(buf[size:])
                size += len(opt)
            data.append(opt)
        ret = cls(len_, data)
        ret.set_nxt(nxt)
        return ret

    def serialize(self):
        buf = struct.pack(self._PACK_STR, self.nxt, self.size)
        buf = bytearray(buf)
        for opt in self.data:
            buf.extend(opt.serialize())
        return buf

    def __len__(self):
        return self._FIX_SIZE + self.size


class option(stringify.StringifyMixin):
    """IPv6 (RFC 2460) Options header encoder/decoder class.

    This is used with ryu.lib.packet.ipv6.hop_opts or
                      ryu.lib.packet.ipv6.dst_opts.

    An instance has the following attributes at least.
    Most of them are same to the on-wire counterparts but in host byte order.
    __init__ takes the corresponding args in this order.

    .. tabularcolumns:: |l|L|

    ============== =======================================
    Attribute      Description
    ============== =======================================
    type\_         option type.
    len\_          the length of data. -1 if type\_ is 0.
    data           an option value. None if len\_ is 0 or -1.
    ============== =======================================
    """

    _PACK_STR = '!BB'
    _MIN_LEN = struct.calcsize(_PACK_STR)

    def __init__(self, type_, len_, data):
        self.type_ = type_
        self.len_ = len_
        self.data = data

    @classmethod
    def parser(cls, buf):
        (type_, ) = struct.unpack_from('!B', buf)
        if not type_:
            cls_ = cls(type_, -1, None)
        else:
            data = None
            (type_, len_) = struct.unpack_from(cls._PACK_STR, buf)
            if len_:
                form = "%ds" % len_
                (data, ) = struct.unpack_from(form, buf, cls._MIN_LEN)
            cls_ = cls(type_, len_, data)
        return cls_

    def serialize(self):
        data = None
        if not self.type_:
            data = struct.pack('!B', self.type_)
        elif not self.len_:
            data = struct.pack(self._PACK_STR, self.type_, self.len_)
        else:
            form = "%ds" % self.len_
            data = struct.pack(self._PACK_STR + form, self.type_,
                               self.len_, self.data)
        return data

    def __len__(self):
        return self._MIN_LEN + self.len_
