"""Artist dates keep source precision and never treat a band ending as a death."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import artist_net


class ArtistFacts(unittest.TestCase):
    def test_precision_and_invalid_dates(self):
        for value in ('1990', '1990-04', '1990-04-12'):
            self.assertEqual(artist_net.life_date(value), value)
        for value in ('1990-02-30', '1990-13', 'unknown', '', '0000'):
            self.assertEqual(artist_net.life_date(value), '')

    def test_person_birth_and_death(self):
        facts = dict(artist_net.facts({'mb_type': 'Person', 'mb_begin': '1946-09-05',
            'mb_end': '1991-11-24', 'mb_ended': True, 'mb_birthplace': 'Zanzibar',
            'mb_deathplace': 'London'}))
        self.assertEqual(facts['Дата рождения'], '1946-09-05')
        self.assertEqual(facts['Дата смерти'], '1991-11-24')
        self.assertIn('Место рождения', facts)
        self.assertIn('Место смерти', facts)

    def test_living_person_and_group(self):
        for data in ({'mb_type': 'Person', 'mb_begin': '1990'},
                     {'mb_type': 'Group', 'mb_begin': '1990', 'mb_end': '2000', 'mb_ended': True}):
            self.assertNotIn('Дата смерти', dict(artist_net.facts(data)))
        self.assertEqual(dict(artist_net.facts(data))['Дата распада'], '2000')

    def test_english_places_and_wikipedia(self):
        with patch.object(artist_net, '_language', return_value='en'), patch.object(artist_net, '_get', return_value=None) as get:
            self.assertEqual(artist_net._ru_place('London'), 'London')
            artist_net._wiki('Example')
            self.assertTrue(all('en.wikipedia.org' in c.args[0] for c in get.call_args_list))

    def test_birthplace_is_not_combined_with_associated_country(self):
        facts = dict(artist_net.facts({'mb_type': 'Person', 'mb_birthplace': 'Stone Town',
            'mb_origin': 'Stone Town', 'mb_country': 'United Kingdom'}))
        self.assertEqual(facts['Место рождения'], 'Stone Town')
        self.assertNotIn('Откуда', facts)
        self.assertIn('Страна / регион', facts)


if __name__ == '__main__':
    unittest.main()
