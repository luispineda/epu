class QueueingMode(object):

    NEVER = "NEVER"
    ALWAYS = "ALWAYS"
    START_ONLY = "START_ONLY"
    RESTART_ONLY = "RESTART_ONLY"


class RestartMode(object):

    NEVER = "NEVER"
    ALWAYS = "ALWAYS"
    ALWAYS_EXCEPT_SYSTEM_RESTART = "ALWAYS_EXCEPT_SYSTEM_RESTART"
    ABNORMAL = "ABNORMAL"
    ABNORMAL_EXCEPT_SYSTEM_RESTART = "ABNORMAL_EXCEPT_SYSTEM_RESTART"
