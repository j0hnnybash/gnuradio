# Copyright 2015-16 Free Software Foundation, Inc.
# This file is part of GNU Radio
#
# SPDX-License-Identifier: GPL-2.0-or-later
#


from ast import literal_eval
from textwrap import dedent

from . import Block, register_build_in
from ._templates import MakoTemplates
from ._flags import Flags

from .. import utils
from ..base import Element

from ._build import build_params


DEFAULT_CODE = '''\
"""
Embedded Python Blocks:

Each time this file is saved, GRC will instantiate the first class it finds
to get ports and parameters of your block. The arguments to __init__  will
be the parameters. All of them are required to have default values!
"""

import numpy as np
from gnuradio import gr


class blk(gr.sync_block):  # other base classes are basic_block, decim_block, interp_block
    """Embedded Python Block example - a simple multiply const"""

    def __init__(self, example_param=1.0):  # only default arguments here
        """arguments to this function show up as parameters in GRC"""
        gr.sync_block.__init__(
            self,
            name='Embedded Python Block',   # will show up in GRC
            in_sig=[np.complex64],
            out_sig=[np.complex64]
        )
        # if an attribute with the same name as a parameter is found,
        # a callback is registered (properties work, too).
        self.example_param = example_param

    def work(self, input_items, output_items):
        """example: multiply with constant"""
        output_items[0][:] = input_items[0] * self.example_param
        return len(output_items[0])
'''

DOC = """
This block represents an arbitrary GNU Radio Python Block.

Its source code can be accessed through the parameter 'Code' which opens your editor. \
Each time you save changes in the editor, GRC will update the block. \
This includes the number, names and defaults of the parameters, \
the ports (stream and message) and the block name and documentation.

Block Documentation:
(will be replaced the docstring of your block class)
"""


@register_build_in
class EPyBlock(Block):

    key = 'epy_block'
    label = 'Python Block'
    exempt_from_id_validation = True  # Exempt epy block from blacklist id validation
    documentation = {'': DOC}

    parameters_data = build_params(
        params_raw=[
            dict(label='Code', id='_source_code', dtype='_multiline_python_external',
                 default=DEFAULT_CODE, hide='part')
        ], have_inputs=True, have_outputs=True, flags=Block.flags, block_id=key
    )
    inputs_data = []
    outputs_data = []

    def __init__(self, flow_graph, **kwargs):
        super(EPyBlock, self).__init__(flow_graph, **kwargs)
        self.states['_io_cache'] = ''

        self.module_name = self.name
        self._epy_reload_error = None

    def rewrite(self):
        print("rewrite start", self.params)
        Element.rewrite(self)

        param_src = self.params['_source_code']

        src = param_src.get_value()

        print("rewriting", self.params)
        # Note: we can use get_evaluated() since all params have been evaluated by Elemen.rewrite(self) above.
        block_params = {k: param.get_evaluated() for k, param in self.params.items() if _is_epy_block_param(param)}
        try:
            print(f"about to extract!")
            blk_io = utils.epy_block_io.extract(src, block_params)
            print(f"extracted blk_io: {blk_io}")

        except Exception as e:
            print(f"extraction failed: {e}")
            self._epy_reload_error = ValueError(str(e))
            try:  # Load last working block io
                blk_io_args = literal_eval(self.states['_io_cache'])
                if len(blk_io_args) == 6:
                    blk_io_args += ([],)  # add empty callbacks
                blk_io = utils.epy_block_io.BlockIO(*blk_io_args)
            except Exception:
                return
        else:
            self._epy_reload_error = None  # Clear previous errors
            self.states['_io_cache'] = repr(tuple(blk_io))

        # print "Rewriting embedded python block {!r}".format(self.name)

        self.label = blk_io.name or blk_io.cls
        self.documentation = {'': blk_io.doc}

        self.module_name = "{}_{}".format(
            self.parent_flowgraph.get_option("id"), self.name)
        self.templates['imports'] = 'import {} as {}  # embedded python block'.format(
            self.module_name, self.name)
        self.templates['make'] = '{mod}.{cls}({args})'.format(
            mod=self.name,
            cls=blk_io.cls,
            args=', '.join('{0}=${{ {0} }}'.format(key) for key, _ in blk_io.params))
        self.templates['callbacks'] = [
            '{0} = ${{ {0} }}'.format(attr) for attr in blk_io.callbacks
        ]

        self._update_params(blk_io.params)
        self._update_ports('in', self.sinks, blk_io.sinks, 'sink')
        self._update_ports('out', self.sources, blk_io.sources, 'source')

        super(EPyBlock, self).rewrite()
        print("rewrite done", self.params)

    def _update_params(self, params_in_src):
        print(f"update params: {params_in_src}")
        param_factory = self.parent_platform.make_param
        params = {}
        for key, value in self.params.copy().items():
            if _is_epy_block_param(value):
                params[key] = value
                del self.params[key]

        for id_, value in params_in_src:
            try:
                param = params[id_]
                if param.default == param.value:
                    param.set_value(value)
                param.default = str(value)
            except KeyError:  # need to make a new param
                param = param_factory(
                    parent=self, id=id_, dtype='raw', value=value,
                    name=id_.replace('_', ' ').title(),
                )
                _mark_as_epy_block_param(param)
            self.params[id_] = param

    def _update_ports(self, label, ports, port_specs, direction):
        port_factory = self.parent_platform.make_port
        ports_to_remove = list(ports)
        iter_ports = iter(ports)
        ports_new = []
        port_current = next(iter_ports, None)
        # FIXME(jlrb): now that we can change the vlen of an existing
        # block via parameter we probably want to reuse the
        # appropriate port and connections instead of breaking them.
        # I wonder how this worked when we simply changed the default
        # in the code block?
        #
        # This seems to break the connections as well, only non
        # embedded blocks behave differently.
        #
        # I seem to recall from 3.10.10 that this would not
        # happen... #NotMyBug?  Actually checking old versions I had
        # previously installed, I believe that this always happened
        # for epy blocks.
        #
        #
        # Also now the block parameter is initially set to the default
        # value, after changing it a couple of times *headscratch*
        #
        # It appears that the initial display of a "None" value in the
        # block parameters is a display/GUI issue. If you save and
        # reopen the block gets the correct default parameter,
        # alternatively if you have the parameter dialog open, simply
        # pressing ok will set the correct value. This appears to be a
        # regression introduced between 3.10.10 and 3.10.11.
        #
        # Note: QT GUI behaves differently, it is less eager to
        # reflect code changes in the UI and requires some random GUI
        # interaction to pick up on added/removed init_args, however
        # it always shows the correct value directly.  CORRECTION:
        # actually no, the issue is also reproducible though more
        # complicated using QT GUI, if you open the parameter dialog
        # for a different block the newly added parameter initially
        # appears with no value and the value is only filled in once
        # you again open some other parameter dialog or similar!).
        #
        #
        # NOTE to self: QT GUI requires missing dependency python-qtpy on
        # archlinux which is only listed for gnuradio-git in the AUR,
        # even though the official package also has QT support
        # enabled, so maybe python-qtpy could be added as optional dependency?
        for key, port_type, vlen in port_specs:
            reuse_port = (
                port_current is not None and
                port_current.dtype == port_type and
                port_current.vlen == vlen and
                (key.isdigit() or port_current.key == key)
            )
            if reuse_port:
                ports_to_remove.remove(port_current)
                port, port_current = port_current, next(iter_ports, None)
            else:
                n = dict(name=label + str(key), dtype=port_type, id=key)
                if port_type == 'message':
                    n['name'] = key
                    n['optional'] = '1'
                if vlen > 1:
                    n['vlen'] = str(vlen)
                port = port_factory(self, direction=direction, **n)
            ports_new.append(port)
        # replace old port list with new one
        del ports[:]
        ports.extend(ports_new)
        # remove excess port connections
        self.parent_flowgraph.disconnect(*ports_to_remove)

    def validate(self):
        super(EPyBlock, self).validate()
        if self._epy_reload_error:
            self.params['_source_code'].add_error_message(
                str(self._epy_reload_error))


def _is_epy_block_param(param):
    return hasattr(param, '__epy_param__')


def _mark_as_epy_block_param(param):
    print(f"marking {param} as epy block param")
    setattr(param, '__epy_param__', True)


@register_build_in
class EPyModule(Block):
    key = 'epy_module'
    label = 'Python Module'
    exempt_from_id_validation = True  # Exempt epy module from blacklist id validation
    documentation = {'': dedent("""
        This block lets you embed a python module in your flowgraph.

        Code you put in this module is accessible in other blocks using the ID of this
        block. Example:

        If you put

            a = 2

            def double(arg):
                return 2 * arg

        in a Python Module Block with the ID 'stuff' you can use code like

            stuff.a  # evals to 2
            stuff.double(3)  # evals to 6

        to set parameters of other blocks in your flowgraph.
    """)}

    flags = Flags(Flags.SHOW_ID)

    parameters_data = build_params(
        params_raw=[
            dict(label='Code', id='source_code', dtype='_multiline_python_external',
                 default='# this module will be imported in the into your flowgraph',
                 hide='part')
        ], have_inputs=False, have_outputs=False, flags=flags, block_id=key
    )

    def __init__(self, flow_graph, **kwargs):
        super(EPyModule, self).__init__(flow_graph, **kwargs)
        self.module_name = self.name

    def rewrite(self):
        super(EPyModule, self).rewrite()
        self.module_name = "{}_{}".format(
            self.parent_flowgraph.get_option("id"), self.name)
        self.templates['imports'] = 'import {} as {}  # embedded python module'.format(
            self.module_name, self.name)
