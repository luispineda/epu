#!/usr/bin/env python

"""
@file ion/data/backends/test/test_cassandra.py
@author Paul Hubbard
@author Dorian Raymer
@test Service only test of Cassandra datastore
"""

import logging
logging = logging.getLogger(__name__)
from uuid import uuid4

from twisted.trial import unittest
from twisted.internet import defer

from ion.data.backends import cassandra

class CassandraStoreTest(unittest.TestCase):

    @defer.inlineCallbacks
    def setUp(self):
        clist = ['amoeba.ucsd.edu:9160']
        self.ds = yield cassandra.CassandraStore.create_store(cass_host_list=clist)
        self.key = str(uuid4())
        self.value = str(uuid4())

    @defer.inlineCallbacks
    def tearDown(self):
        yield self.ds.remove(self.key)
        del self.ds

    @defer.inlineCallbacks
    def test_get_404(self):
        # Make sure we can't read the not-written
        rc = yield self.ds.get(self.key)
        self.assertEqual(rc, None)

    @defer.inlineCallbacks
    def test_write_and_delete(self):
        # Hmm, simplest op, just looking for exceptions
        yield self.ds.put(self.key, self.value)

    @defer.inlineCallbacks
    def test_remove(self):
        yield self.ds.put(self.key, self.value)
        yield self.ds.remove(self.key)
        rc = yield self.ds.get(self.key)
        self.assertEqual(rc, None)
        yield self.ds.remove(self.key)
        yield self.ds.remove('non_exist23231')

    @defer.inlineCallbacks
    def test_put_get_delete(self):
        # Write, then read to verify same
        yield self.ds.put(self.key, self.value)
        b = yield self.ds.get(self.key)
        self.assertEqual(self.value, b)

    @defer.inlineCallbacks
    def test_query(self):
        # Write a key, query for it, verify contents
        yield self.ds.put(self.key, self.value)
        rl = yield self.ds.query(self.key)
        self.assertEqual(rl[0][0], self.key)

class CassandraStoreNSTest(CassandraStoreTest):
    @defer.inlineCallbacks
    def setUp(self):
        clist = ['amoeba.ucsd.edu:9160']
        self.ds = yield cassandra.CassandraStore.create_store(
            cass_host_list=clist,
            namespace='n')
        self.key = str(uuid4())
        self.value = str(uuid4())

class CassandraStoreSCTest(CassandraStoreTest):
    @defer.inlineCallbacks
    def setUp(self):
        clist = ['amoeba.ucsd.edu:9160']
        self.ds = yield cassandra.CassandraStore.create_store(
            cass_host_list=clist,
            keyspace='DatastoreTest',
            colfamily='DS1',
            cf_super=True,
            namespace='n')
        self.key = str(uuid4())
        self.value = str(uuid4())
