"""经纬度解析检查。"""
import unittest
import location_input


class LocationInputTest(unittest.TestCase):
    def test_decimal_with_various_symbols(self):
        cases = [
            ('26.53610N', 26.5361),
            ('26.53610°N', 26.5361),
            ('26.53610°', 26.5361),
            ('北纬26.53610', 26.5361),
            ('北纬26.53610°', 26.5361),
            ('107.9308E', 107.9308),
            ('107.9308°E', 107.9308),
            ('东经107.9308', 107.9308),
            ('东经107.9308°', 107.9308),
        ]
        for inp, expected in cases:
            parsed = location_input.degrees(inp, 180)
            self.assertAlmostEqual(parsed, expected, places=4, msg=inp)

    def test_dms_formats(self):
        parsed = location_input.degrees('北纬26°32′10″', 90)
        self.assertAlmostEqual(parsed, 26 + 32/60 + 10/3600, places=4)

    def test_location_conversion(self):
        res = location_input.location('凯里市', '26.53610N', '107.9308E')
        self.assertEqual(res['city'], '凯里市')
        self.assertTrue(26 < res['anchor_lat'] < 27)
        self.assertTrue(107 < res['anchor_lon'] < 108)


if __name__ == '__main__':
    unittest.main()
