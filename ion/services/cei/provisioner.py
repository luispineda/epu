#!/usr/bin/env python

"""
@file ion/services/cei/provisioner.py
@author Michael Meisinger
@author Alex Clemesha
@brief Starts, stops, and tracks instance and context state.
"""

import logging
from magnet.spawnable import Receiver
from ion.services.base_service import BaseService

logging.basicConfig(level=logging.DEBUG)
logging.debug('Loaded: '+__name__)

class ProvisionerService(BaseService):
    """Provisioner service interface
    """
    pass

# Direct start of the service as a process with its default name
receiver = Receiver(__name__)
instance = ProvisionerService(receiver)
