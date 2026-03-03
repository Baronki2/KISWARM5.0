import unittest
from deployment_script import deploy, rollback

class TestDeployment(unittest.TestCase):

    def test_deployment_success(self):
        result = deploy('production')
        self.assertTrue(result)

    def test_deployment_phase_testing(self):
        phases = ['build', 'test', 'deploy']
        for phase in phases:
            with self.subTest(phase=phase):
                result = deploy(phase)
                self.assertTrue(result)

    def test_rollback_on_failure(self):
        # Simulate a failed deployment
        result = deploy('production', fail=True)
        self.assertFalse(result)
        rollback_result = rollback()
        self.assertTrue(rollback_result)

    def test_error_handling(self):
        with self.assertRaises(Exception):
            deploy('invalid_phase')

if __name__ == '__main__':
    unittest.main()