"""连接串校验检查：不连接任何真实数据库。"""
import unittest

import check_database


class FakeConnector:
    def __init__(self):
        self.calls = []

    def __call__(self, dsn, **kwargs):
        self.calls.append((dsn, kwargs))
        return 'connected'


class ConnectionStringTest(unittest.TestCase):
    def test_local_host_allowed_without_tls(self):
        connector = FakeConnector()
        result = check_database.connect_database('postgresql://user:pw@127.0.0.1:5432/db', connector)
        self.assertEqual(result, 'connected')
        self.assertEqual(connector.calls, [('postgresql://user:pw@127.0.0.1:5432/db',
                                            {'connect_timeout': 10})])

    def test_localhost_and_ipv6_loopback_allowed(self):
        for dsn in ('postgresql://user:pw@localhost/db',
                    'postgresql://user:pw@[::1]/db'):
            connector = FakeConnector()
            self.assertEqual(check_database.connect_database(dsn, connector), 'connected', dsn)

    def test_other_hosts_still_rejected(self):
        for dsn in ('postgresql://user:pw@evil.example.com/db',
                    'postgresql://user:pw@127.0.0.1.evil.com/db',
                    'mysql://user:pw@127.0.0.1/db',
                    'postgresql://user@127.0.0.1/db',
                    'postgresql://user:***@127.0.0.1/db'):
            with self.assertRaises(ValueError, msg=dsn):
                check_database.connect_database(dsn, FakeConnector())

    def test_neon_host_is_not_rejected_as_invalid(self):
        # 部分环境取不到 CA 文件；此处只要求失败原因不是"连接串非法"。
        try:
            check_database.connect_database('postgresql://user:pw@ep-example.neon.tech/db',
                                            FakeConnector())
        except ValueError as error:
            self.assertEqual(str(error), 'trusted_ca_unavailable')


if __name__ == '__main__':
    unittest.main()
