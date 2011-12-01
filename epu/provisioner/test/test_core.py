#!/usr/bin/env python


import uuid
import time

from libcloud.compute.types import InvalidCredsError
import unittest

import ion.util.ionlog
from nimboss.ctx import BrokerError, ContextNotFoundError

from epu.ionproc.dtrs import DeployableTypeLookupError
from epu.provisioner.core import ProvisionerCore, update_nodes_from_context, \
    update_node_ip_info
from epu.provisioner.store import ProvisionerStore
from epu.states import InstanceState
from epu.provisioner.test.util import FakeProvisionerNotifier, \
    FakeNodeDriver, FakeContextClient, make_launch, make_node, \
    make_launch_and_nodes
from epu.test import Mock

log = ion.util.ionlog.getLogger(__name__)

# alias for shorter code
states = InstanceState

class ProvisionerCoreRecoveryTests(unittest.TestCase):

    def setUp(self):
        self.notifier = FakeProvisionerNotifier()
        self.store = ProvisionerStore()
        self.ctx = FakeContextClient()
        self.driver = FakeNodeDriver()
        self.dtrs = FakeDTRS()
        drivers = {'fake' : self.driver}
        self.core = ProvisionerCore(store=self.store, notifier=self.notifier,
                                    dtrs=self.dtrs, site_drivers=drivers,
                                    context=self.ctx)

    def test_recover_launch_incomplete(self):
        """Ensures that launches in REQUESTED state are completed
        """
        launch_id = _new_id()
        doc = "<cluster><workspace><name>node</name><image>fake</image>"+\
              "<quantity>3</quantity>"+\
              "</workspace><workspace><name>running</name><image>fake"+\
              "</image><quantity>1</quantity></workspace></cluster>"
        context = {'broker_uri' : _new_id(), 'context_id' : _new_id(),
                  'secret' : _new_id(), 'uri' : _new_id()}

        requested_node_ids = [_new_id(), _new_id()]

        node_records = [make_node(launch_id, states.RUNNING,
                                              site='fake',
                                              ctx_name='running'),
                        make_node(launch_id, states.REQUESTED,
                                              site='fake',
                                              node_id=requested_node_ids[0],
                                              ctx_name='node'),
                        make_node(launch_id, states.REQUESTED,
                                              site='fake',
                                              node_id=requested_node_ids[1],
                                              ctx_name='node'),
                        make_node(launch_id, states.RUNNING,
                                              ctx_name='node')]
        launch_record = make_launch(launch_id, states.REQUESTED,
                                                node_records, document=doc,
                                                context=context)

        self.store.put_launch(launch_record)
        self.store.put_nodes(node_records)

        # 2 nodes are in REQUESTED state, so those should be launched
        self.core.recover()

        # because we rely on IaaS idempotency, we get full Node responses
        # for all nodes in the group. What really would cause this scenario
        # is successfully launching the full group but failing before records
        # could be written for the two REQUESTED nodes.
        self.assertEqual(3, len(self.driver.created))
        iaas_ids = set(node.id for node in self.driver.created)
        self.assertEqual(3, len(iaas_ids))

        for node_id in requested_node_ids:
            node = self.store.get_node(node_id)
            self.assertEqual(states.PENDING, node['state'])
            self.assertTrue(node['iaas_id'] in iaas_ids)

        launch = self.store.get_launch(launch_id)
        self.assertEqual(states.PENDING, launch['state'])

    def test_recovery_nodes_terminating(self):
        launch_id = _new_id()

        terminating_iaas_id = _new_id()

        node_records = [make_node(launch_id, states.TERMINATING,
                                              iaas_id=terminating_iaas_id,
                                              site='fake'),
                        make_node(launch_id, states.TERMINATED),
                        make_node(launch_id, states.RUNNING)]

        launch_record = make_launch(launch_id, states.RUNNING,
                                                node_records)

        self.store.put_launch(launch_record)
        self.store.put_nodes(node_records)

        self.core.recover()

        self.assertEqual(1, len(self.driver.destroyed))
        self.assertEqual(self.driver.destroyed[0].id, terminating_iaas_id)

        terminated = self.store.get_nodes(state=states.TERMINATED)
        self.assertEqual(2, len(terminated))

    def test_recovery_launch_terminating(self):
        launch_id = _new_id()

        terminating_iaas_ids = [_new_id(), _new_id()]

        node_records = [make_node(launch_id, states.TERMINATING,
                                              iaas_id=terminating_iaas_ids[0],
                                              site='fake'),
                        make_node(launch_id, states.TERMINATED),
                        make_node(launch_id, states.RUNNING,
                                              iaas_id=terminating_iaas_ids[1],
                                              site='fake')]

        launch_record = make_launch(launch_id, states.TERMINATING,
                                                node_records)

        self.store.put_launch(launch_record)
        self.store.put_nodes(node_records)

        self.core.recover()

        self.assertEqual(2, len(self.driver.destroyed))
        self.assertTrue(self.driver.destroyed[0].id in terminating_iaas_ids)
        self.assertTrue(self.driver.destroyed[1].id in terminating_iaas_ids)

        terminated = self.store.get_nodes(state=states.TERMINATED)
        self.assertEqual(3, len(terminated))

        launch_record = self.store.get_launch(launch_id)
        self.assertEqual(launch_record['state'], states.TERMINATED)

    def test_terminate_all(self):
        running_launch_id = _new_id()
        running_launch, running_nodes = make_launch_and_nodes(
                running_launch_id, 3, states.RUNNING)
        self.store.put_launch(running_launch)
        self.store.put_nodes(running_nodes)

        pending_launch_id = _new_id()
        pending_launch, pending_nodes = make_launch_and_nodes(
                pending_launch_id, 3, states.PENDING)
        self.store.put_launch(pending_launch)
        self.store.put_nodes(pending_nodes)

        terminated_launch_id = _new_id()
        terminated_launch, terminated_nodes = make_launch_and_nodes(
                terminated_launch_id, 3, states.TERMINATED)
        self.store.put_launch(terminated_launch)
        self.store.put_nodes(terminated_nodes)

        self.core.terminate_all()

        self.assertEqual(6, len(self.driver.destroyed))

        all_launches = self.store.get_launches()
        self.assertEqual(3, len(all_launches))
        self.assertTrue(all(l['state'] == states.TERMINATED
                           for l in all_launches))

        all_nodes = self.store.get_nodes()
        self.assertEqual(9, len(all_nodes))
        self.assertTrue(all(n['state'] == states.TERMINATED
                           for n in all_nodes))

        state = self.core.check_terminate_all()
        self.assertTrue(state)


class ProvisionerCoreTests(unittest.TestCase):
    """Testing the provisioner core functionality
    """
    def setUp(self):
        self.notifier = FakeProvisionerNotifier()
        self.store = ProvisionerStore()
        self.ctx = FakeContextClient()
        self.dtrs = FakeDTRS()

        self.site1_driver = FakeNodeDriver()
        self.site2_driver = FakeNodeDriver()

        drivers = {'site1' : self.site1_driver, 'site2' : self.site2_driver}
        self.core = ProvisionerCore(store=self.store, notifier=self.notifier,
                                    dtrs=self.dtrs, context=self.ctx,
                                    site_drivers=drivers)

    def test_prepare_dtrs_error(self):
        self.dtrs.error = DeployableTypeLookupError()

        nodes = {"i1" : dict(ids=[_new_id()], site="chicago", allocation="small")}
        request = dict(launch_id=_new_id(), deployable_type="foo",
                       subscribers=('blah',), nodes=nodes)
        self.core.prepare_provision(request)
        self.assertTrue(self.notifier.assure_state(states.FAILED))

    def test_prepare_broker_error(self):
        self.ctx.create_error = BrokerError("fake ctx create failed")
        self.dtrs.result = {'document' : "<fake>document</fake>",
                            "nodes" : {"i1" : {}}}
        nodes = {"i1" : dict(ids=[_new_id()], site="site1", allocation="small")}
        request = dict(launch_id=_new_id(), deployable_type="foo",
                       subscribers=('blah',), nodes=nodes)
        self.core.prepare_provision(request)
        self.assertTrue(self.notifier.assure_state(states.FAILED))

    def test_prepare_execute(self):
        self._prepare_execute()
        self.assertTrue(self.notifier.assure_state(states.PENDING))

    def test_prepare_execute_iaas_fail(self):
        self.site1_driver.create_node_error = InvalidCredsError()
        self._prepare_execute()
        self.assertTrue(self.notifier.assure_state(states.FAILED))

    def _prepare_execute(self):
        self.dtrs.result = {'document' : _get_one_node_cluster_doc("node1", "image1"),
                            "nodes" : {"node1" : {}}}
        request_node = dict(ids=[_new_id()], site="site1", allocation="small")
        request_nodes = {"node1" : request_node}
        request = dict(launch_id=_new_id(), deployable_type="foo",
                       subscribers=('blah',), nodes=request_nodes)

        launch, nodes = self.core.prepare_provision(request)

        self.assertEqual(len(nodes), 1)
        node = nodes[0]
        self.assertEqual(node['node_id'], request_node['ids'][0])
        self.assertEqual(launch['launch_id'], request['launch_id'])

        self.assertTrue(self.ctx.last_create)
        self.assertEqual(launch['context'], self.ctx.last_create)
        for key in ('uri', 'secret', 'context_id', 'broker_uri'):
            self.assertIn(key, launch['context'])
        self.assertTrue(self.notifier.assure_state(states.REQUESTED))

        self.core.execute_provision(launch, nodes)

    def test_execute_bad_doc(self):
        ctx = self.ctx.create()
        launch_record = {
                'launch_id' : "thelaunchid",
                'document' : "<this><isnt><a><real><doc>",
                'deployable_type' : "dt",
                'context' : ctx,
                'subscribers' : [],
                'state' : states.PENDING,
                'node_ids' : ['node1']}
        nodes = [{'node_id' : 'node1', 'launch_id' : "thelaunchid",
                  'state' : states.REQUESTED}]

        self.core.execute_provision(launch_record, nodes)
        self.assertTrue(self.notifier.assure_state(states.FAILED))

        # TODO this should be a better error coming from nimboss
        #self.assertEqual(self.notifier.nodes['node1']['state_desc'], "CONTEXT_DOC_INVALID")

    def test_execute_bad_doc_nodes(self):
        ctx = self.ctx.create()
        launch_record = {
                'launch_id' : "thelaunchid",
                'document' : _get_one_node_cluster_doc("node1", "image1"),
                'deployable_type' : "dt",
                'context' : ctx,
                'subscribers' : [],
                'state' : states.PENDING,
                'node_ids' : ['node1']}
        nodes = [{'node_id' : 'node1', 'launch_id' : "thelaunchid",
                  'state' : states.REQUESTED, 'ctx_name' : "adifferentname"}]

        self.core.execute_provision(launch_record, nodes)
        self.assertTrue(self.notifier.assure_state(states.FAILED))

    def test_execute_bad_doc_node_count(self):
        ctx = self.ctx.create()
        launch_record = {
                'launch_id' : "thelaunchid",
                'document' : _get_one_node_cluster_doc("node1", "image1"),
                'deployable_type' : "dt",
                'context' : ctx,
                'subscribers' : [],
                'state' : states.PENDING,
                'node_ids' : ['node1']}

        # two nodes where doc expects 1
        nodes = [{'node_id' : 'node1', 'launch_id' : "thelaunchid",
                  'state' : states.REQUESTED, 'ctx_name' : "node1"},
                 {'node_id' : 'node1', 'launch_id' : "thelaunchid",
                  'state' : states.REQUESTED, 'ctx_name' : "node1"}]

        self.core.execute_provision(launch_record, nodes)
        self.assertTrue(self.notifier.assure_state(states.FAILED))


    def test_query_missing_node_within_window(self):
        launch_id = _new_id()
        node_id = _new_id()
        ts = time.time() - 30.0
        launch = {'launch_id' : launch_id, 'node_ids' : [node_id],
                'state' : states.PENDING,
                'subscribers' : 'fake-subscribers'}
        node = {'launch_id' : launch_id,
                'node_id' : node_id,
                'state' : states.PENDING,
                'pending_timestamp' : ts}
        self.store.put_launch(launch)
        self.store.put_node(node)

        self.core.query_one_site('fake-site', [node],
                driver=FakeEmptyNodeQueryDriver())
        self.assertEqual(len(self.notifier.nodes), 0)
    
    def test_query_missing_node_past_window(self):
        launch_id = _new_id()
        node_id = _new_id()

        ts = time.time() - 120.0
        launch = {
                'launch_id' : launch_id, 'node_ids' : [node_id],
                'state' : states.PENDING,
                'subscribers' : 'fake-subscribers'}
        node = {'launch_id' : launch_id,
                'node_id' : node_id,
                'state' : states.PENDING,
                'pending_timestamp' : ts}
        self.store.put_launch(launch)
        self.store.put_node(node)

        self.core.query_one_site('fake-site', [node],
                driver=FakeEmptyNodeQueryDriver())
        self.assertEqual(len(self.notifier.nodes), 1)
        self.assertTrue(self.notifier.assure_state(states.FAILED))

    def test_query(self):
        launch_id = _new_id()
        node_id = _new_id()

        iaas_node = self.site1_driver.create_node()[0]
        self.site1_driver.set_node_running(iaas_node.id)

        ts = time.time() - 120.0
        launch = {
                'launch_id' : launch_id, 'node_ids' : [node_id],
                'state' : states.PENDING,
                'subscribers' : 'fake-subscribers'}
        node = {'launch_id' : launch_id,
                'node_id' : node_id,
                'state' : states.PENDING,
                'pending_timestamp' : ts,
                'iaas_id' : iaas_node.id,
                'site':'site1'}

        req_node = {'launch_id' : launch_id,
                'node_id' : _new_id(),
                'state' : states.REQUESTED}
        nodes = [node, req_node]
        self.store.put_launch(launch)
        self.store.put_node(node)
        self.store.put_node(req_node)

        self.core.query_one_site('site1', nodes)

        node = self.store.get_node(node_id)
        self.assertEqual(node.get('public_ip'), iaas_node.public_ip)
        self.assertEqual(node.get('private_ip'), iaas_node.private_ip)
        self.assertEqual(node.get('state'), states.STARTED)

        # query again should detect no changes
        self.core.query_one_site('site1', nodes)

        # now destroy
        self.core.terminate_nodes([node_id])
        node = self.store.get_node(node_id)
        self.core.query_one_site('site1', [node])

        node = self.store.get_node(node_id)
        self.assertEqual(node['public_ip'], iaas_node.public_ip)
        self.assertEqual(node['private_ip'], iaas_node.private_ip)
        self.assertEqual(node['state'], states.TERMINATED)


    def test_query_ctx(self):
        node_count = 3
        launch_id = _new_id()
        node_records = [make_node(launch_id, states.STARTED)
                for i in range(node_count)]
        launch_record = make_launch(launch_id, states.PENDING,
                                                node_records)

        self.store.put_launch(launch_record)
        self.store.put_nodes(node_records)

        self.ctx.expected_count = len(node_records)
        self.ctx.complete = False
        self.ctx.error = False

        #first query with no ctx nodes. zero records should be updated
        self.core.query_contexts()
        self.assertTrue(self.notifier.assure_record_count(0))
        
        # all but 1 node have reported ok
        self.ctx.nodes = [_one_fake_ctx_node_ok(node_records[i]['public_ip'], 
            _new_id(),  _new_id()) for i in range(node_count-1)]

        self.core.query_contexts()
        self.assertTrue(self.notifier.assure_state(states.RUNNING))
        self.assertEqual(len(self.notifier.nodes), node_count-1)

        #last node reports ok
        self.ctx.nodes.append(_one_fake_ctx_node_ok(node_records[-1]['public_ip'],
            _new_id(), _new_id()))

        self.ctx.complete = True
        self.core.query_contexts()
        self.assertTrue(self.notifier.assure_state(states.RUNNING))
        self.assertTrue(self.notifier.assure_record_count(1))
    
    def test_query_ctx_error(self):
        node_count = 3
        launch_id = _new_id()
        node_records = [make_node(launch_id, states.STARTED)
                for i in range(node_count)]
        launch_record = make_launch(launch_id, states.PENDING,
                                                node_records)

        self.store.put_launch(launch_record)
        self.store.put_nodes(node_records)

        self.ctx.expected_count = len(node_records)
        self.ctx.complete = False
        self.ctx.error = False

        # all but 1 node have reported ok
        self.ctx.nodes = [_one_fake_ctx_node_ok(node_records[i]['public_ip'], 
            _new_id(),  _new_id()) for i in range(node_count-1)]
        self.ctx.nodes.append(_one_fake_ctx_node_error(node_records[-1]['public_ip'],
            _new_id(), _new_id()))

        ok_ids = [node_records[i]['node_id'] for i in range(node_count-1)]
        error_ids = [node_records[-1]['node_id']]

        self.ctx.complete = True
        self.ctx.error = True

        self.core.query_contexts()
        self.assertTrue(self.notifier.assure_state(states.RUNNING, ok_ids))
        self.assertTrue(self.notifier.assure_state(states.RUNNING_FAILED, error_ids))

    def test_query_ctx_nodes_not_started(self):
        launch_id = _new_id()
        node_records = [make_node(launch_id, states.PENDING)
                for i in range(3)]
        node_records.append(make_node(launch_id, states.STARTED))
        launch_record = make_launch(launch_id, states.PENDING,
                                                node_records)
        self.store.put_launch(launch_record)
        self.store.put_nodes(node_records)

        self.core.query_contexts()

        # ensure that no context was actually queried. See the note in
        # _query_one_context for the reason why this is important.
        self.assertEqual(len(self.ctx.queried_uris), 0)

    def test_query_ctx_permanent_broker_error(self):
        node_count = 3
        launch_id = _new_id()
        node_records = [make_node(launch_id, states.STARTED)
                for i in range(node_count)]
        node_ids = [node['node_id'] for node in node_records]
        launch_record = make_launch(launch_id, states.PENDING,
                                                node_records)
        self.store.put_launch(launch_record)
        self.store.put_nodes(node_records)

        self.ctx.query_error = ContextNotFoundError()
        self.core.query_contexts()

        self.assertTrue(self.notifier.assure_state(states.RUNNING_FAILED, node_ids))
        launch = self.store.get_launch(launch_id)
        self.assertEqual(launch['state'], states.FAILED)

    def test_update_node_ip_info(self):
        node = dict(public_ip=None)
        iaas_node = Mock(public_ip=None, private_ip=None)
        update_node_ip_info(node, iaas_node)
        self.assertEqual(node['public_ip'], None)
        self.assertEqual(node['private_ip'], None)

        iaas_node = Mock(public_ip=["pub1"], private_ip=["priv1"])
        update_node_ip_info(node, iaas_node)
        self.assertEqual(node['public_ip'], "pub1")
        self.assertEqual(node['private_ip'], "priv1")

        iaas_node = Mock(public_ip=[], private_ip=[])
        update_node_ip_info(node, iaas_node)
        self.assertEqual(node['public_ip'], "pub1")
        self.assertEqual(node['private_ip'], "priv1")

    def test_update_nodes_from_ctx(self):
        launch_id = _new_id()
        nodes = [make_node(launch_id, states.STARTED)
                for i in range(5)]
        ctx_nodes = [_one_fake_ctx_node_ok(node['public_ip'], _new_id(), 
            _new_id()) for node in nodes]

        self.assertEquals(len(nodes), len(update_nodes_from_context(nodes, ctx_nodes)))
        
    def test_update_nodes_from_ctx_with_hostname(self):
        launch_id = _new_id()
        nodes = [make_node(launch_id, states.STARTED)
                for i in range(5)]
        #libcloud puts the hostname in the public_ip field
        ctx_nodes = [_one_fake_ctx_node_ok(ip=_new_id(), hostname=node['public_ip'],
            pubkey=_new_id()) for node in nodes]

        self.assertEquals(len(nodes), len(update_nodes_from_context(nodes, ctx_nodes)))

    def test_query_broker_exception(self):
        for i in range(2):
            launch_id = _new_id()
            node_records = [make_node(launch_id, states.STARTED)]
            launch_record = make_launch(launch_id, states.PENDING,
                                                    node_records)

            self.store.put_launch(launch_record)
            self.store.put_nodes(node_records)

        # no guaranteed order here so grabbing first launch from store
        # and making that one return a BrokerError during context query.
        # THe goal is to ensure that one error doesn't prevent querying
        # for other contexts.

        launches = self.store.get_launches(state=states.PENDING)
        error_launch = launches[0]
        error_launch_ctx = error_launch['context']['uri']
        ok_node_id = launches[1]['node_ids'][0]
        ok_node = self.store.get_node(ok_node_id)

        self.ctx.uri_query_error[error_launch_ctx] = BrokerError("bad broker")
        self.ctx.nodes = [_one_fake_ctx_node_ok(ok_node['public_ip'],
            _new_id(), _new_id())]
        self.ctx.complete = True
        self.core.query_contexts()

        launches = self.store.get_launches()
        for launch in launches:
            self.assertIn(launch['context']['uri'], self.ctx.queried_uris)

            if launch['launch_id'] == error_launch['launch_id']:
                self.assertEqual(launch['state'], states.PENDING)
                expected_node_state = states.STARTED
            else:
                self.assertEqual(launch['state'], states.RUNNING)
                expected_node_state = states.RUNNING

            node = self.store.get_node(launch['node_ids'][0])
            self.assertEqual(node['state'], expected_node_state)

    def test_query_ctx_without_valid_nodes(self):

        # if there are no nodes < TERMINATING, no broker query should happen
        for i in range(3):
            launch_id = _new_id()
            node_records = [make_node(launch_id, states.STARTED)]
            launch_record = make_launch(launch_id, states.PENDING,
                                                    node_records)

            self.store.put_launch(launch_record)
            self.store.put_nodes(node_records)

        launches = self.store.get_launches(state=states.PENDING)
        error_launch = launches[0]

        # mark first launch's node as TERMINATING, should prevent
        # context query and result in launch being marked FAILED
        error_launch_node = self.store.get_node(error_launch['node_ids'][0])
        error_launch_node['state'] = states.TERMINATING
        self.store.put_node(error_launch_node)

        self.core.query_contexts()
        self.assertNotIn(error_launch['context']['uri'], self.ctx.queried_uris)

        launches = self.store.get_launches()
        for launch in launches:
            if launch['launch_id'] == error_launch['launch_id']:
                self.assertEqual(launch['state'], states.FAILED)
                expected_node_state = states.TERMINATING
            else:
                self.assertEqual(launch['state'], states.PENDING)
                expected_node_state = states.STARTED

            node = self.store.get_node(launch['node_ids'][0])
            self.assertEqual(node['state'], expected_node_state)


    def test_query_unexpected_exception(self):
        launch_id = _new_id()
        node_records = [make_node(launch_id, states.STARTED)]
        launch_record = make_launch(launch_id, states.PENDING,
                                                node_records)
        self.store.put_launch(launch_record)
        self.store.put_nodes(node_records)
        self.ctx.query_error = ValueError("bad programmer")


        # digging into internals a bit: patching one of the methods query()
        # calls to raise an exception. This will let us ensure exceptions do
        # not bubble up
        def raiser(self):
            raise KeyError("notreallyaproblem")
        self.core.query_nodes = raiser

        self.core.query() # ensure that exception doesn't bubble up

    def test_dump_state(self):
        node_ids = []
        node_records = []
        for i in range(3):
            launch_id = _new_id()
            nodes = [make_node(launch_id, states.PENDING)]
            node_ids.append(nodes[0]['node_id'])
            node_records.extend(nodes)
            launch = make_launch(launch_id, states.PENDING,
                                                    nodes)
            self.store.put_launch(launch)
            self.store.put_nodes(nodes)

        self.core.dump_state(node_ids[:2])

        # should have gotten notifications about the 2 nodes
        self.assertEqual(self.notifier.nodes_rec_count[node_ids[0]], 1)
        self.assertEqual(node_records[0], self.notifier.nodes[node_ids[0]])
        self.assertEqual(node_records[1], self.notifier.nodes[node_ids[1]])
        self.assertEqual(self.notifier.nodes_rec_count[node_ids[1]], 1)
        self.assertNotIn(node_ids[2], self.notifier.nodes)

    def test_mark_nodes_terminating(self):
        launch_id = _new_id()
        node_records = [make_node(launch_id, states.RUNNING)
                        for i in range(3)]
        launch_record = make_launch(launch_id, states.PENDING,
                                                node_records)

        self.store.put_launch(launch_record)
        self.store.put_nodes(node_records)

        first_two_node_ids = [node_records[0]['node_id'],
                              node_records[1]['node_id']]
        self.core.mark_nodes_terminating(first_two_node_ids)

        self.assertTrue(self.notifier.assure_state(states.TERMINATING,
                                                   nodes=first_two_node_ids))
        self.assertNotIn(node_records[2]['node_id'], self.notifier.nodes)

        for node_id in first_two_node_ids:
            terminating_node = self.store.get_node(node_id)
            self.assertEqual(terminating_node['state'], states.TERMINATING)


def _one_fake_ctx_node_ok(ip, hostname, pubkey):
    identity = Mock(ip=ip, hostname=hostname, pubkey=pubkey)
    return Mock(ok_occurred=True, error_occurred=False, identities=[identity])

def _one_fake_ctx_node_error(ip, hostname, pubkey):
    identity = Mock(ip=ip, hostname=hostname, pubkey=pubkey)
    return Mock(ok_occurred=False, error_occurred=True, identities=[identity],
            error_code=42, error_message="bad bad fake error")


class FakeEmptyNodeQueryDriver(object):
    def list_nodes(self):
        return []


class FakeDTRS(object):
    def __init__(self):
        self.result = None
        self.error = None

    def lookup(self, dt, nodes=None, vars=None):
        if self.error is not None:
            raise self.error

        if self.result is not None:
            return self.result

        raise Exception("bad fixture: nothing to return")


def _new_id():
    return str(uuid.uuid4())


_ONE_NODE_CLUSTER_DOC = """
<cluster>
  <workspace>
    <name>%s</name>
    <quantity>%d</quantity>
    <image>%s</image>
    <ctx></ctx>
  </workspace>
</cluster>
"""

def _get_one_node_cluster_doc(name, imagename, quantity=1):
    return _ONE_NODE_CLUSTER_DOC % (name, quantity, imagename)
    
