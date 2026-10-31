import unittest

from douban.rebuild_pid import collect_people
from douban.validator import match_date, match_year, process_slash_str


class ValidatorTests(unittest.TestCase):
    def test_process_slash_str_keeps_foreign_names(self):
        self.assertEqual(
            'Frank Darabont/蒂姆·罗宾斯',
            process_slash_str(' Frank Darabont / 蒂姆·罗宾斯 '),
        )

    def test_date_and_year_matching(self):
        self.assertEqual('1994', match_year('(1994)'))
        self.assertEqual('1994-9-10', match_date('1994-9-10(加拿大)'))

    def test_collect_people_deduplicates_credits(self):
        people = collect_people([
            {
                'actor_ids': 'Actor A:1|Actor B:2',
                'director_ids': 'Director:3|Actor A:1',
            }
        ])
        self.assertEqual(
            [('1', 'Actor A'), ('2', 'Actor B'), ('3', 'Director')],
            sorted(people),
        )


if __name__ == '__main__':
    unittest.main()

