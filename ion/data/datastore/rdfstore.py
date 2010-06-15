#!/usr/bin/env python
"""
@file ion/data/datastore/rdfstore.py
@author David Stuebe
@author Dorian Raymer

@ Ideas!
Trees & Commits should include the the repo blob! 
"""

import logging

from twisted.internet import defer
from twisted.python import reflect

from ion.data.datastore import cas
from ion.data.datastore import objstore
from ion.data import resource


sha1 = cas.sha1

UUID = objstore.UUID
Blob = objstore.Blob
Tree = objstore.Tree
Entity = objstore.Entity
Commit = objstore.Commit

SUBJECT='subject'
OBJECT='object'
PREDICATE='predicate'

class Association(cas.Tree):
    
    type='association'
    
    entityFactory = cas.Entity
    
    spo = { # Subject, Predicate, Object
        SUBJECT:0,
        PREDICATE:1,
        OBJECT:2
    }
    
    
    def __init__(self, subject, predicate, object):
        
        triple = (subject, predicate, object)
        entities = []
        names = {}
        
        for position in self.spo:
            item = triple[self.spo[position]]

            #if isinstance(item, self.entityFactory):
             #  pass
             
            if isinstance(item, cas.BaseObject):
                child = self.entityFactory(position, item)
            else:
                item = cas.Blob(item)
                child = self.entityFactory(position, item)
        
            entities.append(child)
            names[position] = child
        self.children = entities
        self._names = names
        
    def match(self, other, position=None):
        
        assert isinstance(other, cas.BaseObject)
        
        if not position:
            position = self.spo.keys()
        
        if not getattr(position, '__iter__', False):
            position = (position,)
            
        for pos in position:
            
            if self._names[pos][1] == sha1(other.value):
                return True
            
        return False
                
                
                

class SetStoreContextWrapper(object):
    """
    Context wrapper around backend store.
    """

    def __init__(self, backend, prefix):
        """
        @param backend set store instance that provides ion.data.store.ISetStore
        interface.
        @param prefix segment of namespace (the context).
        """
        self.backend = backend
        self.prefix = prefix

    def _key(self, id):
        return self.prefix + id

    def smembers(self, id):
        return self.backend.smembers(self._key(id))

    def sadd(self, id, val):
        return self.backend.sadd(self._key(id), val)

    def remove(self, id):
        return self.backend.remove(self._key(id))

    def scard(self, id):
        return self.backend.card(self._key(id))

class RdfChassis(objstore.ObjectChassis):
    """
    """
    baseClass = resource.BaseResource

    @defer.inlineCallbacks
    def write_tree(self):
        """
        write current index
        Add refs to blobs in the association set
        """
        obs = self.index.encode()
        blobs = [(name, Blob(val)) for name, val in obs]
        for n, b in blobs:
            yield self.objstore.put(b)

        childs = [Entity(name, blob) for name, blob in blobs]
        tree = Tree(*childs)
        tree_id = yield self.objstore.put(tree)
        
        # Add references to the blobs in the association table
        for n, b in blobs:
            yield self.objstore.associations.sadd(sha1(b), tree_id)
        
        defer.returnValue(tree_id)

    @defer.inlineCallbacks
    def commit(self, working_tree=None):
        """
        """
        id = yield self.write_tree()
        if self.cur_commit:
            parents = [self.cur_commit]
        else:
            parents = []
        commit = Commit(id, parents)
        commit_id = yield self.objstore.put(commit)
        
        # Add references to the tree (associations) from the head
        yield self.objstore.references.sadd(id, self.objstore.partition)
        
        yield self.update_head(commit_id)
        defer.returnValue(commit_id)

    
class RdfStore(objstore.ObjectStore):
    """
    """
    TYPES = {
            UUID.type:UUID,
            Association.type:Association,
            Blob.type:Blob,
            Tree.type:Tree,
            Commit.type:Commit,
            }

    def __init__(self, backend, setbackend, partition=''):
        """
        @param backend instance providing ion.data.store.IStore interface.
        @param partition Logical name spacing in an otherwise flat
        key/value space.
        @note Design decision on qualifying/naming a store name space (like
        a git repository tree)
        """
        cas.CAStore.__init__(self, backend, partition)
        self.partition = partition
        self.storemeta = cas.StoreContextWrapper(backend, partition + '.meta.')
        self.type = reflect.fullyQualifiedName(self.__class__)
        
        # The set of associations to a particular item
        self.associations = SetStoreContextWrapper(setbackend,partition+'.assoc.')
        # The set of Objects that reference an association
        self.references = SetStoreContextWrapper(setbackend,partition+'.refer.')

        


    @classmethod
    def new(cls, backend, setbackend, name):
        """
        @brief Initialize an Object Store in the backend.  
        This is a major operation, like formating a blank hard drive; In
        general, this only needs to be done once to a backend.
        @retval A Deferred that succeeds with a new instance.
        """
        inst = cls(backend, setbackend, name)
        d = inst._store_exists()

        def _succeed(result):
            if not result:
                d = inst._init_store_metadata()
                return d 
            return _fail(result)

        def _fail(result):
            return defer.fail(ObjectStoreError('Partition name already exists'))

        d.addCallback(_succeed)
        d.addErrback(lambda r: _fail(r))
        return d
    
    @defer.inlineCallbacks
    def _create_object(self, name, baseClass):
        """
        @note Not Using Object Store Partition Namespace Yet.
        Might not need to
        """
        refs = cas.StoreContextWrapper(self.backend, name + '.refs.')
        meta = cas.StoreContextWrapper(self.backend, name + '.meta.')
        uuid_obj = UUID(name)
        id = yield self.put(uuid_obj)
        yield self.objs.put(name, id)
        
        obj = yield RdfChassis.new(self, refs, meta, baseClass)
        defer.returnValue(obj)

@defer.inlineCallbacks
def _test(ns):
    from ion.data import store
    from ion.data import set_store
    s = yield store.Store.create_store()

    ss = yield set_store.SetStore.create_store()

    obs = yield RdfStore.new(s, ss, 'test_partition')
    obj = yield obs.create('thing', resource.IdentityResource)
    ind = yield obj.checkout()
    ind.name = 'Carlos S'
    ind.email = 'carlos@ooici.biz'
#    yield obj.commit()
#    ind = yield obj.checkout()
#    ind.name = 'wwww S'
#    ind.email = 'carlos@ooici.biz'
#    yield obj.commit()
#    ind = yield obj.checkout()
#    ind.name = 'Carly S'
#    ind.email = 'carlos@ooici.com'
#    yield obj.commit()
#    ind = yield obj.checkout()
    ns.update(locals())
