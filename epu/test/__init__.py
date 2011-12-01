
import os
import unittest

FIXTURES_ROOT = 'fixtures'

class FileFixtures(object):
    def __init__(self, subdir=None):
        test_root = os.path.abspath(os.path.dirname(__file__))
        self.root = os.path.join(test_root, FIXTURES_ROOT)
        if subdir:
            self.root = os.path.join(self.root, subdir)
        assert os.path.exists(self.root), "No test fixtures?: " + self.root

    def path(self, name):
        return os.path.join(self.root, name)


class Mock(object):
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def __repr__(self):
        return self.__str__()
    def __str__(self):
        return "Mock(" + ",".join("%s=%s" %(k,v) for k,v in self.__dict__.iteritems()) + ")"


def cassandra_test(func):
    """Decorator that skips cassandra integration tests when config is not present
    """
    skip = unittest.SkipTest("Cassandra tests are unconditionally skipped")

    def f(*args, **kwargs):
        raise skip
    return f

# otherwise nosetests will collect this and try to run it
cassandra_test. __test__ = False
