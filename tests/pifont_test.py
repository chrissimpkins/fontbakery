#!/usr/bin/env python
# coding: utf-8
# Copyright 2013,2017 The Font Bakery Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# See AUTHORS.txt for the list of Authors and LICENSE.txt for the License.
import unittest
import os

from fontbakery.pifont import PiFont


class PiFontTest(unittest.TestCase):

    def setUp(self):
        self.font = PiFont('data/test/cousine/Cousine-Bold.ttf')

    def test_get_contours_count(self):
        self.assertEqual(
            self.font.get_contours_count('a'),
            2
        )
        self.assertEqual(
            self.font.get_contours_count('aacute'),
            3
        )
        self.assertEqual(
            self.font.get_contours_count('adieresis'),
            4
        )        

if __name__ == '__main__':
    unittest.main()