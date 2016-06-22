"""
Module Description
==================

Provides classes that are used to construct processing *workflows* (networks, directed acyclic graphs) from registered
from processing *steps* including Python callables, Python expressions, external processes, and other workflows.

This module provides the following data types:

* A :py:class:`Node` has zero or more *inputs* and zero or more *outputs* and can be invoked
* A :py:class:`Workflow` is a ``Node`` that is composed of ``Step`` objects
* A :py:class:`Step` is a ``Node`` that is part of a ``Workflow`` and performs some kind of data processing.
* A :py:class:`OpStep` is a ``Step`` that invokes a Python operation (any callable).
* A :py:class:`ExprStep` is a ``Step`` that executes a Python expression string.
* A :py:class:`WorkflowStep` is a ``Step`` that executes a ``Workflow`` loaded from an external (JSON) resource.
* A :py:class:`NodeConnector` belongs to exactly one ``Node``. Node connectors represent both the named inputs and
  outputs of node. A node connector has a name, a property ``source``, and a property ``value``.
  If ``source`` is set, it must be another ``NodeConnector`` that provides the actual connector's value.
  The value of the ``value`` property can be basically anything that has an external (JSON) representation.

Technical Requirements
======================

A workflow is required to specify its inputs and outputs. Input source may be left unspecified, while it is mandatory to
connect the workflow's outputs to outputs of contained step nodes or inputs of the workflow.

A workflow's step nodes are required to specify all of their input sources. Valid input sources for a step node
are the workflow's inputs or other step node's outputs, or constant values.

Step node inputs and workflow outputs are indicated in the input specification of a node's external JSON
representation:

* ``{"source": "NODE_ID.NAME" }``: the output named *NAME* of another node given by *NODE_ID*.
* ``{"source": ".NAME" }``: a workflow input named *NAME*.
* ``{"value": NUM|STR|LIST|DICT|null }``: a constant (JSON) value.

Workflows are callable by the CLI in the same way as single operations. The command line form for calling an
operation is currently:::

    ect run OP|WORKFLOW [ARGS]

Where *OP* is a registered operation and *WORKFLOW* is a JSON file containing a JSON workflow representation.

Module Reference
================
"""

from abc import ABCMeta, abstractmethod
from collections import OrderedDict
from io import IOBase
from typing import Sequence, Optional, Union, List

from ect.core import Monitor
from .op import OP_REGISTRY, OpMetaInfo, OpRegistration
from .util import Namespace


class Node(metaclass=ABCMeta):
    """
    Base class for all nodes including parent nodes (e.g. :py:class:`Workflow`) and child nodes (e.g. :py:class:`Step`).

    All nodes have inputs and outputs, and can be invoked to perform some operation.

    Inputs and outputs are exposed as attributes of the :py:attr:`input` and :py:attr:`output` properties and
    are both of type :py:class:`NodeConnector`.

    :param op_meta_info: Meta-information about the operation, see :py:class:`OpMetaInfo`.
    :param node_id: A node ID. If None, a unique name will be generated.
    """

    def __init__(self,
                 op_meta_info: OpMetaInfo,
                 node_id: str):
        if not op_meta_info:
            raise ValueError('op_meta_info must be given')
        if not node_id:
            raise ValueError('node_id must be given')
        self._op_meta_info = op_meta_info
        self._id = node_id
        self._input = self._new_input_namespace()
        self._output = self._new_output_namespace()

    @property
    def op_meta_info(self) -> OpMetaInfo:
        """The node's operation meta-information."""
        return self._op_meta_info

    @property
    def id(self) -> str:
        """The node's operation meta-information."""
        return self._id

    @property
    def input(self) -> Namespace:
        """The node's inputs."""
        return self._input

    @property
    def output(self) -> Namespace:
        """The node's outputs."""
        return self._output

    @property
    def root_node(self) -> 'Node':
        """The root_node node."""
        node = self
        while node.parent_node:
            node = node.parent_node
        return node

    @property
    def parent_node(self) -> 'Node':
        """The node's parent node or ``None`` if this node has no parent."""
        return None

    def find_node(self, node_id) -> 'Node':
        """Find a (child) node with the given *node_id*."""
        return None

    @abstractmethod
    def invoke(self, monitor: Monitor = Monitor.NULL):
        """
        Invoke this node's underlying operation with input values from
        :py:attr:`input`. Output values in :py:attr:`output` will
        be set from the underlying operation's return value(s).

        :param monitor: An optional progress monitor.
        """

    @abstractmethod
    def to_json_dict(self):
        """
        Return a JSON-serializable dictionary representation of this object.

        :return: A JSON-serializable dictionary
        """

    def resolve_source_refs(self):
        """Resolve unresolved source references in inputs and outputs."""
        for node_output in self._output[:]:
            node_output.resolve_source_ref()
        for node_input in self._input[:]:
            node_input.resolve_source_ref()

    def find_connector(self, name):
        """Resolve unresolved source references in inputs and outputs."""
        if name in self._output:
            return self._output[name]
        if name in self._input:
            return self._input[name]
        return None

    def __str__(self):
        """String representation."""
        return self.id

    @abstractmethod
    def __repr__(self):
        """String representation for developers."""

    def _new_input_namespace(self):
        return self._new_namespace(self.op_meta_info.input.keys())

    def _new_output_namespace(self):
        return self._new_namespace(self.op_meta_info.output.keys())

    def _new_namespace(self, names):
        return Namespace([(name, NodeConnector(self, name)) for name in names])


class Workflow(Node):
    """
    A workflow of (connected) steps.

    :param name_or_op_meta_info: Qualified operation name or meta-information object of type :py:class:`OpMetaInfo`.
    """

    def __init__(self, name_or_op_meta_info: Union[str, OpMetaInfo]):
        if isinstance(name_or_op_meta_info, str):
            op_meta_info = OpMetaInfo(name_or_op_meta_info)
        else:
            op_meta_info = name_or_op_meta_info
        super(Workflow, self).__init__(op_meta_info, op_meta_info.qualified_name)
        self._steps = OrderedDict()

    @property
    def steps(self) -> List['Step']:
        return list(self._steps.values())

    def find_node(self, step_id) -> 'Step':
        # is it the ID of one of the direct children?
        if step_id in self._steps:
            return self._steps[step_id]
        # is it the ID of one of the children of the children?
        for node in self._steps.values():
            other_node = node.find_node(step_id)
            if other_node:
                return other_node
        return None

    def add_steps(self, *steps: Sequence['Step']):
        for step in steps:
            self._steps[step.id] = step
            step._parent_node = self

    def remove_step(self, step: 'Step'):
        if step.id in self._steps:
            self._steps.remove(step.id)
            step._parent_node = None

    def resolve_source_refs(self):
        """Resolve unresolved source references in inputs and outputs."""
        super(Workflow, self).resolve_source_refs()
        for node in self._steps.values():
            node.resolve_source_refs()

    def invoke(self, monitor=Monitor.NULL):
        """
        Invoke this workflow by invoking all all of its step nodes.
        The node invocation order is determined by the input requirements of individual nodes.

        :param monitor: An optional progress monitor.
        """
        steps = self.steps
        step_count = len(steps)
        if step_count == 1:
            steps[0].invoke(monitor)
        elif step_count > 1:
            monitor.start("Executing workflow '%s'" % self.id, step_count)
            for step in steps:
                step.invoke(monitor.child(1))
            monitor.done()

    @classmethod
    def load(cls, file_path_or_fp: Union[str, IOBase], registry=OP_REGISTRY) -> 'Workflow':
        """
        Load a workflow from a file or file pointer. The format is expected to be "Workflow JSON".

        :param file_path_or_fp: file path or file pointer
        :param registry: Operation registry
        :return: a workflow
        """
        import json
        if isinstance(file_path_or_fp, str):
            file_path = file_path_or_fp
            with open(file_path) as fp:
                json_dict = json.load(fp)
        else:
            fp = file_path_or_fp
            json_dict = json.load(fp)
        return Workflow.from_json_dict(json_dict, registry=registry)

    @classmethod
    def from_json_dict(cls, workflow_json_dict, registry=OP_REGISTRY):
        # Developer note: keep variable naming consistent with Workflow.to_json_dict() method

        qualified_name = workflow_json_dict.get('qualified_name', None)
        if qualified_name is None:
            raise ValueError('missing mandatory property "qualified_name" in Workflow-JSON')
        header_json_dict = workflow_json_dict.get('header', {})
        input_json_dict = workflow_json_dict.get('input', {})
        output_json_dict = workflow_json_dict.get('output', {})
        steps_json_list = workflow_json_dict.get('steps', [])

        # convert 'data_type' entries to Python types in op_meta_info_input_json_dict & node_output_json_dict
        input_obj_dict = OpMetaInfo.json_dict_to_object_dict(input_json_dict)
        output_obj_dict = OpMetaInfo.json_dict_to_object_dict(output_json_dict)
        op_meta_info = OpMetaInfo(qualified_name,
                                  has_monitor=True,
                                  header_dict=header_json_dict,
                                  input_dict=input_obj_dict,
                                  output_dict=output_obj_dict)

        # parse all step nodes
        steps = []
        step_count = 0
        for step_json_dict in steps_json_list:
            step_count += 1
            node = None
            for node_class in [OpStep, WorkflowStep, ExprStep]:
                node = node_class.from_json_dict(step_json_dict, registry=registry)
                if node is not None:
                    steps.append(node)
                    break
            if node is None:
                raise ValueError("unknown type for node #%s in workflow '%s'" % (step_count, qualified_name))

        workflow = Workflow(op_meta_info)
        workflow.add_steps(*steps)

        for node_input in workflow.input[:]:
            node_input.from_json_dict(input_json_dict)
        for node_output in workflow.output[:]:
            node_output.from_json_dict(output_json_dict)

        workflow.resolve_source_refs()
        return workflow

    def to_json_dict(self):
        """
        Return a JSON-serializable dictionary representation of this object.

        :return: A JSON-serializable dictionary
        """
        # Developer note: keep variable naming consistent with Workflow.from_json_dict() method

        # convert all inputs to JSON dicts
        input_json_dict = OrderedDict()
        for node_input in self._input[:]:
            node_input_json_dict = node_input.to_json_dict()
            if node_input.name in self.op_meta_info.output:
                node_input_json_dict.update(self.op_meta_info.input[node_input.name])
            input_json_dict[node_input.name] = node_input_json_dict

        # convert all outputs to JSON dicts
        output_json_dict = OrderedDict()
        for node_output in self._output[:]:
            node_output_json_dict = node_output.to_json_dict()
            if node_output.name in self.op_meta_info.output:
                node_output_json_dict.update(self.op_meta_info.output[node_output.name])
            output_json_dict[node_output.name] = node_output_json_dict

        # convert all step nodes to JSON dicts
        steps_json_list = []
        for step in self._steps.values():
            steps_json_list.append(step.to_json_dict())

        # convert 'data_type' Python types entries to JSON-strings
        input_json_dict = OpMetaInfo.object_dict_to_json_dict(input_json_dict)
        output_json_dict = OpMetaInfo.object_dict_to_json_dict(output_json_dict)

        workflow_json_dict = OrderedDict()
        workflow_json_dict['qualified_name'] = self.op_meta_info.qualified_name
        workflow_json_dict['input'] = input_json_dict
        workflow_json_dict['output'] = output_json_dict
        workflow_json_dict['steps'] = steps_json_list

        return workflow_json_dict

    def __str__(self):
        return self.id

    def __repr__(self):
        return "Workflow(%s)" % repr(self.op_meta_info.qualified_name)


class Step(Node):
    """
    A step is an inner node of a workflow.

    :param op_meta_info: Meta-information about the operation, see :py:class:`OpMetaInfo`.
    :param node_id: A node ID. If None, a unique name will be generated.
    """

    def __init__(self, op_meta_info: OpMetaInfo, node_id: str):
        super(Step, self).__init__(op_meta_info, node_id)
        self._parent_node = None

    @property
    def parent_node(self):
        """The node's ID."""
        return self._parent_node

    @classmethod
    def from_json_dict(cls, json_dict, registry=OP_REGISTRY) -> Optional['Step']:
        node = cls.new_step_from_json_dict(json_dict, registry=registry)
        if node is None:
            return None

        node_input_dict = json_dict.get('input', {})
        for name, properties in node_input_dict.items():
            if name not in node.input:
                # update op_meta_info
                node.op_meta_info.input[name] = node.op_meta_info.input.get(name, {})
                # then create a new NodeConnector
                node.input[name] = NodeConnector(node, name)
            node_input = node.input[name]
            node_input.from_json_dict(node_input_dict)

        node_output_dict = json_dict.get('output', {})
        for name, properties in node_output_dict.items():
            if name not in node.output:
                # first update op_meta_info
                node.op_meta_info.output[name] = node.op_meta_info.output.get(name, {})
                # then create a new NodeConnector
                node.output[name] = NodeConnector(node, name)
            node_output = node.output[name]
            node_output.from_json_dict(node_output_dict)

        return node

    @classmethod
    @abstractmethod
    def new_step_from_json_dict(cls, json_dict, registry=OP_REGISTRY) -> Optional['Step']:
        """Create a new step node instance from the given *json_dict*"""

    def to_json_dict(self):
        """
        Return a JSON-serializable dictionary representation of this object.

        :return: A JSON-serializable dictionary
        """

        node_dict = OrderedDict()
        node_dict['id'] = self.id

        self.enhance_json_dict(node_dict)

        node_dict['input'] = OrderedDict(
            [(node_input.name, node_input.to_json_dict()) for node_input in self.input[:]])
        node_dict['output'] = OrderedDict(
            [(node_output.name, node_output.to_json_dict()) for node_output in self.output[:]])

        return node_dict

    @abstractmethod
    def enhance_json_dict(self, node_dict: OrderedDict):
        """Enhance the given JSON-compatible *node_dict* by step specific elements."""

    def __str__(self):
        """String representation."""
        return self.id


class WorkflowStep(Step):
    """
    A `WorkflowStep` is a step node that invokes an externally stored :py:class:`Workflow`.

    :param workflow: The referenced workflow.
    :param resource: A resource (e.g. file path, URL) from which the workflow was loaded.
    :param node_id: A node ID. If None, a unique ID will be generated.
    """

    def __init__(self, workflow, resource, node_id=None):
        if not workflow:
            raise ValueError('workflow must be given')
        if not resource:
            raise ValueError('resource must be given')
        node_id = node_id if node_id else 'workflow_step_' + hex(id(self))[2:]
        super(WorkflowStep, self).__init__(workflow.op_meta_info, node_id)
        self._workflow = workflow
        self._resource = resource
        # Connect the workflow's inputs with this node's input sources
        for workflow_input in workflow.input[:]:
            name = workflow_input.name
            assert name in self.input
            workflow_input.source = self.input[name]

    @property
    def workflow(self) -> 'Workflow':
        """The workflow."""
        return self._workflow

    @property
    def resource(self) -> str:
        """The workflow's resource path (file path, URL)."""
        return self._resource

    def invoke(self, monitor: Monitor = Monitor.NULL):
        """
        Invoke this node's underlying :py:attr:`workflow` with input values from
        :py:attr:`input`. Output values in :py:attr:`output` will
        be set from the underlying workflow's return value(s).

        :param monitor: An optional progress monitor.
        """
        self._workflow.invoke(monitor=monitor)
        # transfer workflow output values into this node's output values
        for workflow_output in self._workflow.output[:]:
            assert workflow_output.name in self.output
            node_output = self.output[workflow_output.name]
            node_output.value = workflow_output.value

    @classmethod
    def new_step_from_json_dict(cls, json_dict, registry=OP_REGISTRY):
        resource = json_dict.get('workflow', None)
        if resource is None:
            return None
        workflow = Workflow.load(resource, registry=registry)
        return WorkflowStep(workflow, resource, node_id=json_dict.get('id', None))

    def enhance_json_dict(self, node_dict: OrderedDict):
        node_dict['workflow'] = self._resource

    def __repr__(self):
        return "WorkflowStep(%s, '%s', node_id='%s')" % (repr(self._workflow), self.resource, self.id)


class OpStep(Step):
    """
    An `OpStep` is a step node that invokes a registered operation of type :py:class:`OpRegistration`.

    :param operation: A fully qualified operation name or operation object such as a class or callable.
    :param registry: An operation registry to be used to lookup the operation, if given by name.
    :param node_id: A node ID. If None, a unique ID will be generated.
    """

    def __init__(self, operation, node_id=None, registry=OP_REGISTRY):
        if not operation:
            raise ValueError('operation must be given')
        if isinstance(operation, str):
            op_registration = registry.get_op(operation, fail_if_not_exists=True)
        elif isinstance(operation, OpRegistration):
            op_registration = operation
        else:
            op_registration = registry.get_op(operation, fail_if_not_exists=True)
        assert op_registration is not None
        node_id = node_id if node_id else 'op_step_' + hex(id(self))[2:]
        op_meta_info = op_registration.meta_info
        super(OpStep, self).__init__(op_meta_info, node_id)
        self._op_registration = op_registration

    @property
    def op(self):
        """The operation registration. See :py:class:`ect.core.op.OpRegistration`"""
        return self._op_registration

    def invoke(self, monitor: Monitor = Monitor.NULL):
        """
        Invoke this node's underlying operation :py:attr:`op` with input values from
        :py:attr:`input`. Output values in :py:attr:`output` will
        be set from the underlying operation's return value(s).

        :param monitor: An optional progress monitor.
        """
        input_values = OrderedDict()
        for node_input in self.input[:]:
            input_values[node_input.name] = node_input.value

        return_value = self._op_registration(monitor=monitor, **input_values)

        if self.op_meta_info.has_named_outputs:
            for output_name, output_value in return_value.items():
                self.output[output_name].value = output_value
        else:
            self.output[OpMetaInfo.RETURN_OUTPUT_NAME].value = return_value

    @classmethod
    def new_step_from_json_dict(cls, json_dict, registry=OP_REGISTRY):
        op_name = json_dict.get('op', None)
        if op_name is None:
            return None
        return cls(op_name, node_id=json_dict.get('id', None), registry=registry)

    def enhance_json_dict(self, node_dict: OrderedDict):
        node_dict['op'] = self.op_meta_info.qualified_name

    def __repr__(self):
        return "OpStep(%s, node_id='%s')" % (self.op_meta_info.qualified_name, self.id)


class ExprStep(Step):
    """
    An ``ExprStep`` is a step node that computes its output from a simple (Python) expression string.

    :param expression: A simple (Python) expression string.
    :param input_dict: input name to input properties mapping.
    :param output_dict: output name to output properties mapping.
    :param node_id: A node ID. If None, a unique ID will be generated.
    """

    def __init__(self, expression, input_dict=None, output_dict=None, node_id=None):
        if not expression:
            raise ValueError('expression must be given')
        node_id = node_id if node_id else 'expr_step_' + hex(id(self))[2:]
        op_meta_info = OpMetaInfo(node_id, input_dict=input_dict, output_dict=output_dict)
        if len(op_meta_info.output) == 0:
            op_meta_info.output[op_meta_info.RETURN_OUTPUT_NAME] = {}
        super(ExprStep, self).__init__(op_meta_info, node_id)
        self._expression = expression

    @property
    def expression(self):
        """The expression."""
        return self._expression

    def invoke(self, monitor: Monitor = Monitor.NULL):
        """
        Invoke this node's underlying operation :py:attr:`op` with input values from
        :py:attr:`input`. Output values in :py:attr:`output` will
        be set from the underlying operation's return value(s).

        :param monitor: An optional progress monitor.
        """
        input_values = OrderedDict()
        for node_input in self.input[:]:
            input_values[node_input.name] = node_input.value

        return_value = eval(self.expression, None, input_values)

        if self.op_meta_info.has_named_outputs:
            for output_name, output_value in return_value.items():
                self.output[output_name].value = output_value
        else:
            self.output[OpMetaInfo.RETURN_OUTPUT_NAME].value = return_value

    @classmethod
    def new_step_from_json_dict(cls, json_dict, registry=OP_REGISTRY):
        expression = json_dict.get('expression', None)
        if expression is None:
            return None
        return cls(expression, node_id=json_dict.get('id', None))

    def enhance_json_dict(self, node_dict: OrderedDict):
        node_dict['expression'] = self.expression

    def __repr__(self):
        return "ExprNode('%s', node_id='%s')" % (self.expression, self.id)


class NodeConnector:
    """Represents a named input or output of a :py:class:`Node`. """

    def __init__(self, node: Node, name: str):
        assert node is not None
        assert name is not None
        assert name in node.op_meta_info.input or name in node.op_meta_info.output
        self._node = node
        self._name = name
        self._source_ref = None
        self._source = None
        self._value = None

    @property
    def node(self) -> Node:
        return self._node

    @property
    def node_id(self) -> Node:
        return self._node.id

    @property
    def name(self) -> str:
        return self._name

    @property
    def value(self):
        if self._source:
            return self._source.value
        else:
            return self._value

    @value.setter
    def value(self, new_value):
        self._value = new_value
        self._source = None
        self._source_ref = None

    @property
    def source(self) -> 'NodeConnector':
        return self._source

    @source.setter
    def source(self, new_source: 'NodeConnector'):
        if self is new_source:
            raise ValueError("cannot connect '%s' with itself" % self)
        self._source = new_source
        self._source_ref = (new_source.node_id, new_source.name) if new_source else None
        self._value = None

    def resolve_source_ref(self):
        if self._source_ref:
            other_node_id, other_name = self._source_ref
            if other_node_id and other_name:
                root_node = self._node.root_node
                other_node = root_node if root_node.id == other_node_id else root_node.find_node(other_node_id)
                if other_node:
                    node_connector = other_node.find_connector(other_name)
                    if node_connector:
                        self.source = node_connector
                        return
                    raise ValueError(
                        "cannot connect '%s' with '%s.%s' because node '%s' has no input/output named '%s'" % (
                            self, other_node_id, other_name, other_node_id, other_name))
                else:
                    raise ValueError("cannot connect '%s' with '%s.%s' because node '%s' does not exist" % (
                        self, other_node_id, other_name, other_node_id))
            elif other_node_id:
                root_node = self._node.root_node
                other_node = root_node if root_node.id == other_node_id else root_node.find_node(other_node_id)
                if other_node:
                    if len(other_node.output) == 1:
                        node_connector = other_node.output[0]
                        self.source = node_connector
                        return
                    else:
                        raise ValueError(
                            "cannot connect '%s' with node '%s' because it has %s outputs" % (
                                self, other_node_id, len(other_node.output)))
                else:
                    raise ValueError("cannot connect '%s' with output of node '%s' because node '%s' does not exist" % (
                        self, other_node_id, other_node_id))
            elif other_name:
                # look for 'other_name' first in this scope and then the parent scopes
                other_node = self._node
                while other_node:
                    node_connector = other_node.find_connector(other_name)
                    if node_connector:
                        self.source = node_connector
                        return
                    other_node = other_node.parent_node
                raise ValueError(
                    "cannot connect '%s' with '.%s' because '%s' does not exist in any scope" % (
                        self, other_name, other_name))

    def from_json_dict(self, json_dict):
        self._source_ref = None
        self._source = None
        self._value = None

        if json_dict is None:
            return

        connector_source_def = json_dict.get(self.name, None)
        if connector_source_def is None:
            return

        if not isinstance(connector_source_def, str):
            connector_json_dict = connector_source_def
            if 'source' in connector_json_dict:
                if 'value' in connector_json_dict:
                    raise ValueError(
                        "error decoding '%s' because \"source\" and \"value\" are mutually exclusive" % self)
                connector_source_def = connector_json_dict['source']
            elif 'value' in connector_json_dict:
                # Care: constant may be converted to a real Python value here
                # Must add converter callback, or so.
                self.value = connector_json_dict['value']
                return
            else:
                return

        source_format_msg = "error decoding '%s' because the \"source\" value format is " \
                            "neither \"<node-id>.<name>\", \"<node-id>\", nor \".<name>\""

        parts = connector_source_def.rsplit('.', maxsplit=1)
        if len(parts) == 1 and parts[0]:
            node_id = parts[0]
            connector_name = None
        elif len(parts) == 2:
            if not parts[1]:
                raise ValueError(source_format_msg % self)
            node_id = parts[0] if parts[0] else None
            connector_name = parts[1]
        else:
            raise ValueError(source_format_msg % self)
        self._source_ref = node_id, connector_name

    def to_json_dict(self):
        """
        Return a JSON-serializable dictionary representation of this object.

        :return: A JSON-serializable dictionary
        """
        json_dict = dict()
        if self._source is not None:
            json_dict['source'] = '%s.%s' % (self._source.node.id, self._source.name)
        elif self._value is not None:
            json_dict['value'] = self._value
        return json_dict

    def __str__(self):
        return "%s.%s" % (self._node.id, self._name)

    def __repr__(self):
        return "NodeConnector(%s, %s)" % (repr(self.node_id), repr(self.name))