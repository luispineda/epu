#!/usr/bin/env python

"""
@file ion/services/base_service.py
@author Michael Meisinger
@brief abstract base classes for all service interfaces, implementations and provider.
"""

import logging
from twisted.internet import defer
from magnet.spawnable import Receiver
from magnet.spawnable import send
from magnet.spawnable import spawn
from magnet.store import Store

from ion.core.base_process import BaseProcess
import ion.util.procutils as pu

class BaseService(BaseProcess):
    """
    This is the abstract superclass for all service processes.

    A service process is a Capability Container process that can be spawned
    anywhere in the network and that provides a service.
    """
    def __init__(self, receiver=None, spawnArgs=None):
        """Constructor using a given name for the spawnable receiver.
        """
        BaseProcess.__init__(self, receiver, spawnArgs)

    def plc_init(self):
        return self.slc_init()

    def slc_init(self):
        """Service life cycle event: on initialization of process (once)
        """
        logging.info('BaseService.slc_init()')

    @classmethod
    def _add_messages(cls):
        pass

    @classmethod
    def _add_conv_type(cls):
        pass
    
    @classmethod
    def service_declare(cls, **kwargs):
        """Helper method to declare service process module attributes
        """
        decl = {}
        decl.update(kwargs)
        return decl

class BaseServiceClient(object):
    """This is the abstract base class for service client libraries.
    """
    def __init__(self, proc=None):
        self.process = proc
    
    def attach(self):
        if self.process and self.process.receiver.spawned:
            pass

class BaseServiceImplementation(object):
    """This is the abstract base class for all service provider implementations
    of a service provider interface.
    """
