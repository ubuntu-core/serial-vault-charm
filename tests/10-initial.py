#!/usr/bin/env python3

import amulet
import requests
import unittest


class TestDeployment(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.deployment = amulet.Deployment()

        cls.deployment.add('postgresql')
        cls.deployment.add('serial-vault')
        cls.deployment.add-relation('serial-vault:database', 'postgresql:db')
        cls.deployment.expose('serial-vault')

        try:
            cls.deployment.setup(timeout=900)
            cls.deployment.sentry.wait()
        except amulet.helpers.TimeoutError:
            amulet.raise_status(
                amulet.SKIP, msg="Environment was not ready in time")
        except:
            raise
        cls.unit = cls.deployment.sentry.unit['serial-vault/0']

    # Test methods would go here.
    def test_100_check_default_configuration(cks):
        """
        Check that the default configuration for the unit is as expected.
        """
        pass


if __name__ == '__main__':
    unittest.main()
