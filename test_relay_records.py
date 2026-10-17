"""中继记录解析检查；不连接 VPS 或运动世界。"""
import unittest

import relay

SAMPLE = """时间               距离m     时长   配速   步频  达标 rrid
10-17 01:36:35     2000  0:13:00   6:30    160   是  abc123
10-13 13:09:19     2000  0:13:00   6:30    160   是  def456
拉取失败: 网络错误
"""


class ParseRowsTest(unittest.TestCase):
    def test_parses_only_real_rows(self):
        rows = relay.parse_rows(SAMPLE)
        self.assertEqual(rows, [
            {'when': '10-17 01:36:35', 'distance_m': 2000, 'duration': '0:13:00', 'pace': '6:30'},
            {'when': '10-13 13:09:19', 'distance_m': 2000, 'duration': '0:13:00', 'pace': '6:30'}])

    def test_returns_no_record_numbers_or_raw_text(self):
        text = repr(relay.parse_rows(SAMPLE))
        self.assertNotIn('abc123', text)
        self.assertNotIn('rrid', text)

    def test_ignores_header_and_errors(self):
        self.assertEqual(relay.parse_rows('时间 距离m 时长\n拉取失败: err'), [])
        self.assertEqual(relay.parse_rows(''), [])

    def test_caps_first_page_at_twenty_rows(self):
        text = '\n'.join(f'10-17 01:36:{index:02d}     2000  0:13:00   6:30    160   是  r{index}'
                         for index in range(30))
        self.assertEqual(len(relay.parse_rows(text)), 20)


if __name__ == '__main__':
    unittest.main()
