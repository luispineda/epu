import ion.util.ionlog
log = ion.util.ionlog.getLogger(__name__)

import random
import time

from twisted.internet import defer

from epu.decisionengine import Engine
from epu.epucontroller import LaunchItem
from epu.ionproc.torque import TorqueManagerClient
import epu.states as InstanceStates

BAD_STATES = [InstanceStates.TERMINATING, InstanceStates.TERMINATED, InstanceStates.FAILED]
TERMINATE_DELAY_SECS = 600

class TorqueOnDemandEngine(Engine):
    """
    A decision engine that looks at queue length.  If there are queued
    jobs, it will launch one instance per job. If there are idle nodes,
    it will terminate them.
    """
    
    def __init__(self):
        super(TorqueOnDemandEngine, self).__init__()
        # todo: get all of this from conf:
        self.available_allocations = ["small"]
        self.available_sites = ["ec2-east"]
        self.available_types = ["epu_work_consumer"]

        self.torque = None # setup in initialize()

        self.free_worker_times = {}
        self.add_worker_times = {}
        self.num_torque_workers = 0
        
    @defer.inlineCallbacks
    def initialize(self, control, state, conf=None):
        """Engine API method"""
        # todo: need central constants for these key strings
        parameters = {"timed-pulse-irregular":5000}
        if conf and conf.has_key("force_site"):
            self.available_sites = [conf["force_site"]]

        if conf and conf.has_key("epuworker_type"):
            self.available_types = [conf["epuworker_type"]]

        if conf and conf.has_key("epuworker_allocation"):
            self.available_allocations = [conf["epuworker_allocation"]]

        if not conf:
            raise Exception("cannot initialize without external configuration")

        # create a client for managing the torque headnode
        if conf.has_key("torque"):
            self.torque = conf['torque']
        else:
            self.torque = None
        if not self.torque:
            self.torque = TorqueManagerClient()
            yield self.torque.attach()

        # first thing to do is subscribe to the torque default queue
        yield self.torque.watch_queue(control.controller_name)

        log.info("Torque on demand engine initialized")
        
        control.configure(parameters)

    @defer.inlineCallbacks
    def decide(self, control, state):
        """Engine API method"""
        all_instance_lists = state.get_all("instance-state")
        all_instance_health = state.get_all("instance-health")

        if all_instance_health:
            health = dict((node.node_id, node) for node in all_instance_health)
        else:
            health = None

        valid_count = 0
        for instance_list in all_instance_lists:
            instance_id = None
            ok = True
            for state_item in instance_list:
                if not instance_id:
                    instance_id = state_item.key
                if state_item.value in BAD_STATES:
                    ok = False
                    break
            if ok and instance_id:
                if health and not health[instance_id].is_ok():
                    self._destroy_one(control, instance_id)
                else:
                    valid_count += 1
        
        # get worker status (free, offline, etc.) info from torque
        worker_status_msgs = state.get_all("worker-status")
        worker_status = self._get_worker_status(worker_status_msgs)
        log.debug("Got worker status message: %s" % worker_status)

        num_pending_instances = self._get_num_pending_instances(state, all_instance_lists)
        log.debug("There are %s pending instances." % num_pending_instances)

        num_queued_jobs = self._get_queuelen(state)
        log.debug("There are %s queued jobs." % num_queued_jobs)

        num_free_workers = self._get_num_free_workers(worker_status)
        log.debug("There are %s free workers." % num_free_workers)

        new_workers = self._get_new_running_workers(state,
                                worker_status, all_instance_lists)
        num_new_workers = len(new_workers)
        log.debug("There are %s new running workers: %s" % (num_new_workers, new_workers))

        log.debug("There are %s torque workers." % self.num_torque_workers)

        # determine the number of instances to launch
        num_instances = num_pending_instances + \
                        self.num_torque_workers + \
                        num_new_workers
        num_instances_to_launch = num_queued_jobs - num_instances
        if num_instances_to_launch > 0:
            log.debug("Attempting to launch %s instances." % num_instances_to_launch)
            for i in range(num_instances_to_launch):
                self._launch_one(control)
                valid_count += 1
        else:
            log.debug("Not launching instances. Offlining free nodes.")
            cur_time = time.time()
            for host in worker_status.keys():
                try:
                    time_diff = cur_time - self.free_worker_times[host]
                except:
                    time_diff = 0
                if (worker_status[host] == 'free') and \
                   (time_diff > TERMINATE_DELAY_SECS):
                    log.debug("Offlining node: %s" % host)
                    yield self.torque.offline_node(host)

        # add new workers to torque
        for host in new_workers:
            self.num_torque_workers += 1
            log.debug("Adding node: %s" % host)
            self.add_worker_times[host] = time.time()
            yield self.torque.add_node(host)

        # note first time nodes move out of the offline state
        for host in worker_status.keys():
            if 'offline' not in worker_status[host]:
                cur_time = time.time()
                if not self.free_worker_times.has_key(host):
                    log.debug('host %s is no longer offline: %s' % (host, cur_time))
                    self.free_worker_times[host] = cur_time

        # terminate nodes
        log.debug("Attempting to remove and terminate all offline nodes.")
        for host in worker_status.keys():
            cur_time = time.time()
            try:
                time_diff = cur_time - self.free_worker_times[host]
            except:
                time_diff = 0
            if (('offline' in worker_status[host]) or \
                (('down' in worker_status[host]) and \
                 (host != 'localhost'))) and \
               (time_diff > TERMINATE_DELAY_SECS):
                self.num_torque_workers -= 1
                if self.free_worker_times.has_key(host):
                    del self.free_worker_times[host]
                if self.add_worker_times.has_key(host):
                    del self.add_worker_times[host]
                log.debug("Removing node: %s" % host)
                yield self.torque.remove_node(host)
                instanceid = state.get_instance_from_ip(host)
                log.debug("Terminating node: %s (%s)" % (instanceid, host))
                self._destroy_one(control, instanceid)
                valid_count -= 1

        # cleanup 
        for host in self.add_worker_times.keys():
            if not self.free_worker_times.has_key(host):
                add_time = self.add_worker_times[host]
                cur_time = time.time()
                kill_time = add_time + TERMINATE_DELAY_SECS
                if cur_time > kill_time:
                    self.num_torque_workers -= 1
                    if self.add_worker_times.has_key(host):
                        del self.add_worker_times[host]
                    log.debug("Removing node (cleanup): %s" % host)
                    yield self.torque.remove_node(host)
                    instanceid = state.get_instance_from_ip(host)
                    log.debug("Terminating node (cleanup): %s (%s)" % (instanceid, host))
                    self._destroy_one(control, instanceid)
                    valid_count -= 1

        txt = "instance"
        if valid_count != 1:
            txt += "s"
        log.debug("Aware of %d running/starting %s" % (valid_count, txt))
            
    def _get_queuelen(self, state):
        all_qlens = state.get_all("queue-length")

        if len(all_qlens) == 0:
            log.debug("no queuelen readings to analyze")
            return 0

        if len(all_qlens) != 1:
            raise Exception("multiple queuelen readings to analyze")

        qlens = all_qlens[0]

        if len(qlens) == 0:
            log.debug("no queuelen readings to analyze")
            return 0

        return qlens[-1].value

    def _get_num_free_workers(self, worker_status):
        num_free = 0
        for worker in worker_status.keys():
            if worker_status[worker] == 'free':
                num_free += 1
        return num_free

    def _get_num_pending_instances(self, state, all_instances):
        pending_states = [InstanceStates.REQUESTING, InstanceStates.REQUESTED,
                          InstanceStates.PENDING, InstanceStates.STARTED,
                          InstanceStates.ERROR_RETRYING]
        num_pending_instances = 0
        for instance in all_instances:
            pending = None
            for state_item in instance:
                host = state.get_instance_public_ip(state_item.key)
                state_value = state_item.value
                if state_value in pending_states:
                    log.debug('pending: instance: %s (%s)' % (host, state_value))
                    if pending == None:
                        pending = True
                if state_value not in pending_states:
                    log.debug('not pending: instance: %s (%s)' % (host, state_value))
                    pending = False
            if pending:
                num_pending_instances += 1
        return num_pending_instances

    def _get_new_running_workers(self, state, worker_status, all_instances):
        new_running_workers = []
        for instance in all_instances:
            for state_item in instance:
                if (state_item.value == InstanceStates.RUNNING) and \
                   (state_item.value not in BAD_STATES):
                    host = state.get_instance_public_ip(state_item.key)
                    log.debug('new running instance: %s (%s)' % (host, state_item.value))
                    if host not in worker_status.keys():
                        new_running_workers.append(host)
        return new_running_workers

    def _get_worker_status(self, worker_status_msgs):
        if len(worker_status_msgs) == 0:
            log.debug("no worker status messages")
            return {}

        if len(worker_status_msgs) != 1:
            raise Exception("multiple worker status messages: %s" % worker_status_msgs)

        worker_status_msg = worker_status_msgs[-1]

        if len(worker_status_msg) == 0:
            log.debug("no worker status strings")
            return {}

        worker_status_str = worker_status_msg[-1].value
        log.debug("worker status string: %s" % worker_status_str)

        if worker_status_str == "":
            log.debug("empty worker status string")
            return {}

        workersplit = worker_status_str.split(';')
        worker_status = {}
        for worker in workersplit:
            host = worker.split(':')[0].strip()
            status = worker.split(':')[1].strip()
            worker_status[host] = status
        return worker_status

    def _launch_one(self, control):
        log.info("Requesting instance")
        launch_description = {}
        launch_description["work_consumer"] = \
                LaunchItem(1, self._allocation(), self._site(), None)
        control.launch(self._deployable_type(), launch_description)
    
    def _pick_instance_to_die(self, all_instance_lists):
        # filter out instances that are in terminating state or 'worse'
        
        candidates = []
        for instance_list in all_instance_lists:
            ok = True
            for state_item in instance_list:
                if state_item.value in BAD_STATES:
                    ok = False
                    break
            if ok:
                candidates.append(state_item.key)
        
        log.debug("Found %d instances that could be killed:\n%s" % (len(candidates), candidates))
        
        if len(candidates) == 0:
            return None
        elif len(candidates) == 1:
            return candidates[0]
        else:
            idx = random.randint(0, len(candidates)-1)
            return candidates[idx]
    
    def _destroy_one(self, control, instanceid):
        log.info("Destroying an instance ('%s')" % instanceid)
        instance_list = [instanceid]
        control.destroy_instances(instance_list)
        
    def _deployable_type(self):
        return self.available_types[0]
        
    def _allocation(self):
        return self.available_allocations[0]
        
    def _site(self):
        return self.available_sites[0]