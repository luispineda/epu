import de_states
import ion.util.ionlog
log = ion.util.ionlog.getLogger(__name__)

import time
import uuid
from collections import defaultdict
from epu.decisionengine import EngineLoader
import epu.states as InstanceStates
from epu import cei_events
from twisted.internet.task import LoopingCall
from twisted.internet import defer
from epu.epucontroller.health import HealthMonitor
from epu.epucontroller import de_states

from forengine import Control
from forengine import State
from forengine import StateItem

PROVISIONER_VARS_KEY = 'provisioner_vars'
MONITOR_HEALTH_KEY = 'monitor_health'
HEALTH_BOOT_KEY = 'health_boot_timeout'
HEALTH_MISSING_KEY = 'health_missing_timeout'
HEALTH_ZOMBIE_KEY = 'health_zombie_timeout'

class ControllerCore(object):
    """Controller functionality that is not specific to the messaging layer.
    """

    def __init__(self, provisioner_client, engineclass, controller_name, conf=None):
        prov_vars = None
        health_kwargs = None
        if conf:
            if conf.has_key(PROVISIONER_VARS_KEY):
                prov_vars = conf[PROVISIONER_VARS_KEY]

            if conf.get(MONITOR_HEALTH_KEY):
                health_kwargs = {}
                if HEALTH_BOOT_KEY in conf:
                    health_kwargs['boot_seconds'] = conf[HEALTH_BOOT_KEY]
                if HEALTH_MISSING_KEY in conf:
                    health_kwargs['missing_seconds'] = conf[HEALTH_MISSING_KEY]
                if HEALTH_ZOMBIE_KEY in conf:
                    health_kwargs['zombie_seconds'] = conf[HEALTH_ZOMBIE_KEY]

        if health_kwargs is not None:
            health_monitor = HealthMonitor(**health_kwargs)
        else:
            health_monitor = None

        self.state = ControllerCoreState(health_monitor)

        # There can only ever be one 'reconfigure' or 'decide' engine call run
        # at ANY time.  The 'decide' call is triggered via timed looping call
        # and 'reconfigure' is triggered asynchronously at any moment.  
        self.busy = defer.DeferredSemaphore(1)
        
        self.control = ControllerCoreControl(provisioner_client, self.state, prov_vars, controller_name)
        self.engine = EngineLoader().load(engineclass)

    def new_sensor_info(self, content):
        """Ingests new sensor information, decides on validity and type of msg.
        """

        # Keeping message differentiation first, before state_item is parsed.
        # There needs to always be a methodical way to differentiate.
        if content.has_key("node_id"):
            self.state.new_instancestate(content)
        elif content.has_key("queue_name"):
            self.state.new_queuelen(content)
        else:
            log.error("received unknown sensor info: '%s'" % content)

    def new_heartbeat(self, content):
        """Ingests new heartbeat information
        """
        self.state.new_heartbeat(content)

    def begin_controlling(self):
        """Call the decision engine at the appropriate times.
        """
        log.debug('Starting engine decision loop - %s second interval',
                self.control.sleep_seconds)
        self.control_loop = LoopingCall(self.run_decide)
        self.control_loop.start(self.control.sleep_seconds, now=False)
        
    def run_initialize(self, conf):
        """Performs initialization routines that may require async processing
        """
        # DE routines can optionally return a Deferred
        return defer.maybeDeferred(self.engine.initialize,
                                   self.control, self.state, conf)

    @defer.inlineCallbacks
    def run_decide(self):
        # update heartbeat states
        self.state.update()

        yield self.busy.run(self.engine.decide, self.control, self.state)
        
    @defer.inlineCallbacks
    def run_reconfigure(self, conf):
        yield self.busy.run(self.engine.reconfigure, self.control, conf)

    def de_state(self):
        if hasattr(self.engine, "de_state"):
            return self.engine.de_state
        else:
            return de_states.UNKNOWN


class ControllerCoreState(State):
    """Keeps data, also what is passed to decision engine.

    In the future the decision engine will be passed more of a "view"
    """

    def __init__(self, health_monitor=None):
        super(ControllerCoreState, self).__init__()
        self.instance_state_parser = InstanceStateParser()
        self.queuelen_parser = QueueLengthParser()
        self.instance_states = defaultdict(list)
        self.queue_lengths = defaultdict(list)

        self.health = health_monitor

    def new_instancestate(self, content):
        state_item = self.instance_state_parser.state_item(content)
        if state_item:
            self.instance_states[state_item.key].append(state_item)

            if self.health:
                # need to send node state information to health monitor too.
                # it uses it to determine when nodes are missing or zombies
                self.health.node_state(state_item.key, state_item.value,
                                       state_item.time)

    def new_launch(self, new_instance_id):
        state = InstanceStates.REQUESTING
        item = StateItem("instance-state", new_instance_id, time.time(), state)
        self.instance_states[item.key].append(item)

    def new_queuelen(self, content):
        state_item = self.queuelen_parser.state_item(content)
        if state_item:
            self.queue_lengths[state_item.key].append(state_item)

    def new_heartbeat(self, content):
        if self.health:
            self.health.new_heartbeat(content)
        else:
            log.info("Got heartbeat but node health isn't monitored: %s",
                     content)

    def update(self):
        if self.health:
            self.health.update()

    def get_all(self, typename):
        """
        Get all data about a particular type.

        State API method, see the decision engine implementer's guide.

        @retval list(StateItem) StateItem instances that match the type
        or an empty list if nothing matches.
        @exception KeyError if typename is unknown
        """
        if typename == "instance-state":
            data = self.instance_states
        elif typename == "queue-length":
            data = self.queue_lengths
        elif typename == "instance-health":
            data = self.health.nodes if self.health else None
        else:
            raise KeyError("Unknown typename: '%s'" % typename)

        if data is not None:
            return data.values()
        else:
            return None

    def get(self, typename, key):
        """Get all data about a particular key of a particular type.

        State API method, see the decision engine implementer's guide.

        @retval list(StateItem) StateItem instances that match the key query
        or an empty list if nothing matches.
        @exception KeyError if typename is unknown
        """
        if typename == "instance-state":
            data = self.instance_states
        elif typename == "queue-length":
            data = self.queue_lengths
        elif typename == "instance-health":
            data = self.health.nodes if self.health else None
        else:
            raise KeyError("Unknown typename: '%s'" % typename)

        if data and data.has_key(key):
            return data[key]
        else:
            return []


class InstanceStateParser(object):
    """Converts instance state message into a StateItem
    """

    def __init__(self):
        pass

    def state_item(self, content):
        log.debug("received new instance state message: '%s'" % content)
        try:
            instance_id = self._expected(content, "node_id")
            state = self._expected(content, "state")
        except KeyError:
            log.error("could not capture sensor info (full message: '%s')" % content)
            return None
        return StateItem("instance-state", instance_id, time.time(), state)

    def _expected(self, content, key):
        if content.has_key(key):
            return str(content[key])
        else:
            log.error("message does not contain part with key '%s'" % key)
            raise KeyError()

class QueueLengthParser(object):
    """Converts queuelen message into a StateItem
    """

    def __init__(self):
        pass

    def state_item(self, content):
        log.debug("received new queulen state message: '%s'" % content)
        try:
            queuelen = self._expected(content, "queue_length")
            queuelen = int(queuelen)
            queueid = self._expected(content, "queue_name")
        except KeyError:
            log.error("could not capture sensor info (full message: '%s')" % content)
            return None
        except ValueError:
            log.error("could not convert queulen into integer (full message: '%s')" % content)
            return None
        return StateItem("queue-length", queueid, time.time(), queuelen)

    def _expected(self, content, key):
        if content.has_key(key):
            return str(content[key])
        else:
            log.error("message does not contain part with key '%s'" % key)
            raise KeyError()


class ControllerCoreControl(Control):

    def __init__(self, provisioner_client, state, prov_vars, controller_name):
        super(ControllerCoreControl, self).__init__()
        self.sleep_seconds = 5.0
        self.provisioner = provisioner_client
        self.state = state
        self.controller_name = controller_name
        self.prov_vars = prov_vars # can be None

    def configure(self, parameters):
        """
        Give the engine the opportunity to offer input about how often it
        should be called or what specific events it would always like to be
        triggered after.

        See the decision engine implementer's guide for specific configuration
        options.

        @retval None
        @exception Exception illegal/unrecognized input
        """
        if not parameters:
            log.info("ControllerCoreControl is configured, no parameters")
            return
            
        if parameters.has_key("timed-pulse-irregular"):
            sleep_ms = int(parameters["timed-pulse-irregular"])
            self.sleep_seconds = sleep_ms / 1000.0
            log.info("Configured to pulse every %.2f seconds" % self.sleep_seconds)
            
        if parameters.has_key(PROVISIONER_VARS_KEY):
            self.prov_vars = parameters[PROVISIONER_VARS_KEY]
            log.info("Configured with new provisioner vars:\n%s" % self.prov_vars)

    def launch(self, deployable_type_id, launch_description, extravars=None):
        """Choose instance IDs for each instance desired, a launch ID and send
        appropriate message to Provisioner.

        Control API method, see the decision engine implementer's guide.

        @param deployable_type_id string identifier of the DP to launch
        @param launch_description See engine implementer's guide
        @param extravars Optional, see engine implementer's guide
        @retval tuple (launch_id, launch_description), see guide
        @exception Exception illegal input
        @exception Exception message not sent
        """

        # right now we are sending some node-specific data in provisioner vars
        # (node_id at least)
        if len(launch_description) != 1:
            raise NotImplementedError("Only single-node launches are supported")

        launch_id = str(uuid.uuid4())
        log.info("Request for DP '%s' is a new launch with id '%s'" % (deployable_type_id, launch_id))
        new_instance_id_list = []
        for group,item in launch_description.iteritems():
            log.info(" - %s is %d %s from %s" % (group, item.num_instances, item.allocation_id, item.site))

            if item.num_instances != 1:
                raise NotImplementedError("Only single-node launches are supported")

            for i in range(item.num_instances):
                new_instance_id = str(uuid.uuid4())
                self.state.new_launch(new_instance_id)
                item.instance_ids.append(new_instance_id)
                new_instance_id_list.append(new_instance_id)
        
        vars_send = self.prov_vars.copy()
        if extravars:
            vars_send.update(extravars)

        # The node_id var is the reason only single-node launches are supported.
        # It could be instead added by the provisioner or something? It also
        # is complicated by the contextualization system.
        vars_send['node_id'] = new_instance_id_list[0]
        vars_send['heartbeat_dest'] = self.controller_name

        log.debug("Launching with parameters:\n%s" % str(vars_send))

        subscribers = (self.controller_name,)
            
        self.provisioner.provision(launch_id, deployable_type_id,
                launch_description, subscribers, vars=vars_send)
        extradict = {"launch_id":launch_id,
                     "new_instance_ids":new_instance_id_list,
                     "subscribers":subscribers}
        cei_events.event("controller", "new_launch",
                         log, extra=extradict)
        return (launch_id, launch_description)

    def destroy_instances(self, instance_list):
        """Terminate particular instances.

        Control API method, see the decision engine implementer's guide.

        @param instance_list list size >0 of instance IDs to terminate
        @retval None
        @exception Exception illegal input/unknown ID(s)
        @exception Exception message not sent
        """
        self.provisioner.terminate_nodes(instance_list)

    def destroy_launch(self, launch_id):
        """Terminate an entire launch.

        Control API method, see the decision engine implementer's guide.

        @param launch_id launch to terminate
        @retval None
        @exception Exception illegal input/unknown ID
        @exception Exception message not sent
        """
        self.provisioner.terminate_launches([launch_id])
