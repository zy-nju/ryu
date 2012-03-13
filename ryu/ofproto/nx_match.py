# Copyright (C) 2011 Nippon Telegraph and Telephone Corporation.
# Copyright (C) 2011, 2012 Isaku Yamahata <yamahata at valinux co jp>
# Copyright (C) 2012 Simon Horman <horms ad verge net au>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import struct

from ryu import exception
from . import ofproto_parser
from . import ofproto_v1_0

import logging
LOG = logging.getLogger('ryu.ofproto.nx_match')


UINT64_MAX = (1 << 64) - 1

FWW_IN_PORT = 1 << 0
FWW_ALL = (1 << 13) - 1

MF_PACK_STRING_BE64 = '!Q'
MF_PACK_STRING_BE16 = '!H'

_MF_FIELDS = {}


class Flow(object):
    pass


class FlowWildcards(object):
    def __init__(self):
        self.wildcards = FWW_ALL


class ClsRule(object):
    def __init__(self):
        self.wc = FlowWildcards()
        self.flow = Flow()


def _set_nxm_headers(nxm_headers):
    '''Annotate corresponding NXM header'''
    def _set_nxm_headers(self):
        self.nxm_headers = nxm_headers
        return self
    return _set_nxm_headers


def _register_make(cls):
    '''class decorator to Register mf make'''
    assert cls.nxm_headers is not None
    assert cls.nxm_headers is not []
    for nxm_header in cls.nxm_headers:
        assert nxm_header not in _MF_FIELDS
        _MF_FIELDS[nxm_header] = cls.make
    return cls


def mf_from_nxm_header(nxm_header):
    if nxm_header not in _MF_FIELDS:
        return None
    make = _MF_FIELDS.get(nxm_header)
    assert make is not None
    return make()


class MFField(object):
    def __init__(self, pack_str):
        self.pack_str = pack_str
        self.n_bytes = struct.calcsize(pack_str)
        self.n_bits = self.n_bytes * 8

    def _put(self, buf, offset, value):
        ofproto_parser.msg_pack_into(self.pack_str, buf, offset, value)
        return self.n_bytes

    def putw(self, buf, offset, value, mask):
        len = self._put(buf, offset, value)
        return len + self._put(buf, offset + len, mask)

    def _is_all_ones(self, value):
        return value == (1 << self.n_bits) - 1

    def putm(self, buf, offset, value, mask):
        if mask == 0:
            return 0
        elif self._is_all_ones(mask):
            return self._put(buf, offset, value)
        else:
            return self.putw(buf, offset, value, mask)


def serialize_nxm_match(rule, buf, offset):
    old_offset = offset

    if not rule.wc.wildcards & FWW_IN_PORT:
        offset += nxm_put(buf, offset, ofproto_v1_0.NXM_OF_IN_PORT, rule)

    # XXX: Ethernet.
    # XXX: 802.1Q
    # XXX: L3

    # Tunnel Id
    if rule.wc.tun_id_mask != 0:
        if rule.wc.tun_id_mask == UINT64_MAX:
            header = ofproto_v1_0.NXM_NX_TUN_ID
        else:
            header = ofproto_v1_0.NXM_NX_TUN_ID_W
        offset += nxm_put(buf, offset, header, rule)

    # XXX: Cookie

    # Pad
    pad_len = round_up(offset) - offset
    ofproto_parser.msg_pack_into("%dx" % pad_len, buf, offset)

    # The returned length, the match_len, does not include the pad
    return offset - old_offset


def nxm_put(buf, offset, header, rule):
    nxm = NXMatch(header)
    len = nxm.put_header(buf, offset)
    mf = mf_from_nxm_header(nxm.header)
    return len + mf.put(buf, offset + len, rule)


def round_up(length):
    return (length + 7) / 8 * 8  # Round up to a multiple of 8


class NXMatch(object):
    def __init__(self, header):
        self.header = header

    @classmethod
    def parse(cls, buf, offset, match_len):
        if match_len < 4:
            raise exception.OFPMalformedMessage
        (header,) = struct.unpack_from(ofproto_v1_0.NXM_HEADER_PACK_STRING,
                                       buf, offset)
        instance = cls(header)
        payload_len = instance.length()
        if payload_len == 0 or match_len < payload_len + 4:
            raise exception.OFPMalformedMessage
        return instance

    def vendor(self):
        return self.header >> 16

    def field(self):
        return (self.header >> 9) % 0x7f

    def type(self):
        return (self.header >> 9) % 0x7fffff

    def hasmask(self):
        return (self.header >> 8) & 1

    def length(self):
        return self.header & 0xff

    def show(self):
        return ('%08x (vendor=%x, field=%x, hasmask=%x len=%x)' %
                (self.header, self.vendor(), self.field(),
                 self.hasmask(), self.length()))

    def put_header(self, buf, offset):
        ofproto_parser.msg_pack_into(ofproto_v1_0.NXM_HEADER_PACK_STRING,
                                     buf, offset, self.header)
        return struct.calcsize(ofproto_v1_0.NXM_HEADER_PACK_STRING)
