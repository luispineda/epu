#!/usr/bin/env python


class InstanceState(object):
    """Instance states
    """

    REQUESTING = '100-REQUESTING'
    """Request has been made but not acknowledged through SA"""

    REQUESTED = '200-REQUESTED'
    """Request has been acknowledged by provisioner"""

    ERROR_RETRYING = '300-ERROR_RETRYING'
    """Request encountered an error but is still being attempted"""

    PENDING = '400-PENDING'
    """Request is pending in IaaS layer"""

    STARTED = '500-STARTED'
    """Instance has been started in IaaS layer"""

    RUNNING = '600-RUNNING'
    """Instance has been contextualized and is operational"""

    RUNNING_FAILED = '650-RUNNING_FAILED'
    """Instance is started in IaaS but contextualization failed"""

    TERMINATING = '700-TERMINATING'
    """Termination of the instance has been requested"""

    TERMINATED = '800-TERMINATED'
    """Instance has been terminated in IaaS layer or before it reached PENDING"""

    FAILED = '900-FAILED'
    """Instance has failed and will not be retried"""

    REJECTED = '950-REJECTED'
    """Instance has been rejected by the Provisioner or IaaS and will not be retried
    """


class InstanceHealthState(object):

    # Health for an instance is unknown. It may be terminated, booting,
    # or health monitoring may even be disabled.
    UNKNOWN = "UNKNOWN"

    # Instance has sent an OK heartbeat within the past missing_timeout
    # seconds, and has sent no errors.
    OK = "OK"

    # Most recent heartbeat from the instance includes an error from the
    # process monitor itself (supervisord)
    MONITOR_ERROR = "MONITOR_ERROR"

    # Most recent heartbeat from the instance includes at least one error
    # from a monitored process
    PROCESS_ERROR = "PROCESS_ERROR"

    # Instance is running but we haven't received a heartbeat for more than
    # missing_timeout seconds
    OUT_OF_CONTACT = "OUT_OF_CONTACT"

    # Instance is running but we haven't received a heartbeat for more than
    # missing_timeout seconds, a dump_state() message was sent, and we
    # subsequently haven't received a heartbeat in really_missing_timeout
    # seconds
    MISSING = "MISSING"

    # Instance is terminated but we have received a heartbeat in the past
    # zombie_timeout seconds
    ZOMBIE = "ZOMBIE"


class DecisionEngineState(object):
    """Decision engine states
    """
    PENDING = 'PENDING_DE'  # EPU is waiting on something

    STABLE = 'STABLE_DE'  # EPU is in a stable state (with respect to its policy)

    UNKNOWN = 'UNKNOWN'  # DE does not implement the contract

    DEVMODE_FAILED = 'DEVMODE_FAILED_DE'  # EPU is in development mode and received a node failure notification


class ProcessState(object):
    """Valid states for processes in the system

    In addition to this state value, each process also has a "round" number.
    This is the number of times the process has been assigned a slot and later
    been ejected (due to failure perhaps).

    These two values together move only in a single direction, allowing
    the system to detect and handle out-of-order messages. The state values are
    ordered and any backwards movement will be accompanied by an increment of
    the round.

    So for example a new process starts in Round 0 and state REQUESTING and
    proceeds through states as it launches:

    Round   State

    0       100-REQUESTING
    0       200-REQUESTED
    0       300-WAITING             process is waiting in a queue
    0       400-PENDING             process is assigned a slot and deploying

    Unfortunately the assigned resource spontaneously catches on fire. When
    this is detected, the process round is incremented and state rolled back
    until a new slot can be assigned. Perhaps it is at least given a higher
    priority.

    1       250-DIED_REQUESTED      process is waiting in the queue
    1       400-PENDING             process is assigned a new slot
    1       500-RUNNING             at long last

    The fire spreads to a neighboring node which happens to be running the
    process. Again the process is killed and put back in the queue.

    2       250-DIED_REQUESTED
    2       300-WAITING             this time there are no more slots


    At this point the client gets frustrated and terminates the process to
    move to another datacenter.

    2       600-TERMINATING
    2       700-TERMINATED

    """
    REQUESTING = "100-REQUESTING"
    """Process request has not yet been acknowledged by Process Dispatcher

    This state will only exist inside of clients of the Process Dispatcher
    """

    REQUESTED = "200-REQUESTED"
    """Process request has been acknowledged by Process Dispatcher

    The process is pending a decision about whether it can be immediately
    assigned a slot or if it must wait for one to become available.
    """

    DIED_REQUESTED = "250-DIED_REQUESTED"
    """Process was >= PENDING but died, waiting for a new slot

    The process is pending a decision about whether it can be immediately
    assigned a slot or if it must wait for one to become available.
    """

    WAITING = "300-WAITING"
    """Process is waiting for a slot to become available

    There were no available slots when this process was reviewed by the
    matchmaker. Processes with the immediate flag set will never reach this
    state and will instead go straight to FAILED.
    """

    PENDING = "400-PENDING"
    """Process is deploying to a slot

    A slot has been assigned to the process and deployment is underway. It
    is quite possible for the resource or process to die before deployment
    succeeds however. Once a process reaches this state, moving back to
    an earlier state requires an increment of the process' round.
    """

    RUNNING = "500-RUNNING"
    """Process is running
    """

    TERMINATING = "600-TERMINATING"
    """Process termination has been requested
    """

    TERMINATED = "700-TERMINATED"
    """Process is terminated
    """

    EXITED = "800-EXITED"
    """Process has finished execution successfully
    """

    FAILED = "850-FAILED"
    """Process request failed
    """

    REJECTED = "900-REJECTED"
    """Process could not be scheduled and it was rejected

    This is the terminal state of processes with the immediate flag when
    no resources are immediately available.
    """


class HAState(object):

    PENDING = "PENDING"
    """HA Process has been requested, but not enough instances of it have been
    started that it is useful
    """

    READY = "READY"
    """HA Process has been requested, and enough instances of it have been started
    that it is useful. It is still scaling, however
    """

    STEADY = "STEADY"
    """HA Process is ready and stable. No longer scaling.
    """

    FAILED = "FAILED"
    """HA Process has been started, but is not able to recover from a problem
    """
