# -*- coding: utf-8 -*-
from collections import namedtuple, Counter
import re
import sqlite3

from bs4 import BeautifulSoup
import click
import requests

PRONOUN_MAP = {
    'their': 'nonbinary',
    'they': 'nonbinary',
    'them': 'nonbinary',
    'her': 'female',
    'she': 'female',
    'his': 'male',
    'him': 'male',
    'he': 'male',
}


DBRow = namedtuple(
    'DBRow',
    ['artist', 'context', 'gender', 'is_group', 'lead', 'members']
)
Artist = namedtuple('Artist', ['name', 'spotify_id', 'wiki_url', 'lastfm_url'])
MemberResults = namedtuple(
    'MemberResults',
    ['nonbinary', 'female', 'male', 'unknown', 'names']
)


class Genderifier(object):
    """Singleton to handle stateful traversing of gender lookups."""

    def __init__(self, spotify_token, lastfm_api_key=None, batch_limit=50,
                 db_file_path=None):
        """Setup."""
        self._db_file_path = db_file_path or '.genderify.db'
        self._conn = None
        self._did_check_db_existing = False
        self._fetched_artists_to_process = []
        self._current_artist_stack = []
        self._spotify_token = spotify_token
        self._batch_limit = batch_limit
        self._lastfm_api_key = lastfm_api_key
        if lastfm_api_key is None:
            self.log("Last.fm lookups disabled, no key given.", fg="red")

    def __enter__(self):
        self._conn = sqlite3.connect(self._db_file_path)
        return self

    def __exit__(self, *args):
        self._conn.close()

    def log(self, msg, fg=None):
        """Log but with indent."""
        msg = " " * len(self._current_artist_stack) + msg
        click.secho(msg, fg=fg)

    def _get_db(self):
        """Just setup the database and return a cursor."""
        curs = self._conn.cursor()
        if not self._did_check_db_existing:
            curs.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND "
                "name='meta'"
            )
            meta_exists = curs.fetchone()
            if not meta_exists:
                curs.execute(
                    """
                    CREATE TABLE meta (
                        id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
                        offset INTEGER

                    )
                    """
                )

            curs.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='artists'"
            )
            artists_exists = curs.fetchone()
            if not artists_exists:
                curs.execute(
                    """
                    CREATE TABLE artists (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT,
                        spotify_id TEXT,
                        wiki_url TEXT,
                        lastfm_url TEXT,
                        context TEXT,
                        gender TEXT,
                        is_group BOOLEAN,
                        lead_gender TEXT,
                        nonbinary_count INT,
                        female_count INT,
                        male_count INT,
                        unknown_count INT,
                        member_names TEXT
                    )
                    """
                )
            self._did_check_db_existing = True
        return curs

    def get_artist_obj_from_name(self, name):
        """Return an Artist object with just the name set."""
        return Artist(
            name=name,
            spotify_id=None,
            wiki_url=None,
            lastfm_url=None
        )

    def _delete_artist(self, name):
        """Delete the artist."""
        curs = self._get_db()
        try:
            curs.execute(
                "DELETE FROM artists WHERE name = ?",
                (name,)
            )
            self._conn.commit()
            return True
        except sqlite3.ProgrammingError as err:
            self.log(err, fg='red')

    def _store_artist(self, row):
        """Store the results."""
        curs = self._get_db()
        try:
            curs.execute(
                """
                INSERT INTO artists(
                    name,
                    spotify_id,
                    wiki_url,
                    lastfm_url,
                    context,
                    gender,
                    is_group,
                    lead_gender,
                    nonbinary_count,
                    female_count,
                    male_count,
                    unknown_count,
                    member_names
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                row
            )
            self._conn.commit()
            return True
        except sqlite3.ProgrammingError as err:
            self.log(err, fg='red')

    def _set_offset(self, offset):
        """Set the offset."""
        curs = self._get_db()
        curs.execute("INSERT INTO meta(offset) VALUES (?)", (offset,))
        self._conn.commit()

    def _get_offset(self):
        """Get the last offset."""
        curs = self._get_db()
        curs.execute("SELECT offset FROM meta ORDER BY timestamp DESC LIMIT 1")
        offset = curs.fetchone()
        return offset[0] if offset else 0

    def _checked_result(self, name):
        """Check to see if we already got this."""
        curs = self._get_db()
        curs.execute(
            "SELECT * FROM artists WHERE name = ?", (name,)
        )
        row = curs.fetchone()
        if row:
            # item 0 is the ID, discard
            result = DBRow(
                Artist(*row[1:5]),
                row[5], row[6], row[7], row[8],
                MemberResults(*row[9:])
            )
            return result

    def _wiki_get_info(self, soup):
        """Get the 'infobox' rows from the RHS of wiki page."""
        info_rows = soup.select('table.infobox tr th[scope="row"]')
        return info_rows

    def _wiki_is_artist_page(self, soup):
        """Determine if a soup is about an artist."""
        is_artist = set(
            ['Genres', 'Labels', 'Instruments']
        ) & set(
            [th.text for th in self._wiki_get_info(soup)]
        )

        return is_artist

    def _wiki_is_disambiguation(self, artist, soup):
        """Return True if this a disambiguation page."""
        name = artist.name.lower()
        text = soup.text.lower()
        if u"{} may refer to:".format(name) in text:
            return True
        if u"{} can refer to:".format(name) in text:
            return True
        return False

    def _wiki_get_disambiguated_artist_soup(self, soup):
        """Get the actual soup from the disambiguation page."""
        return None  # TODO FIXME

    def _wiki_get_artist_soup(self):
        """Try to get the artist page, few options to check..."""
        artist = self._current_artist_stack[-1]
        name = artist.name
        url = artist.wiki_url or u"https://en.wikipedia.org/wiki/{}".format(
            name.replace(' ', '_')
        )
        self.log(u"Trying Wikipedia URL {}...".format(url))
        req = requests.get(url)
        text = req.text
        soup = BeautifulSoup(text, "html.parser")
        continue_checks = True

        self._current_artist_stack[-1] = Artist(  # update with current url
            name, artist.spotify_id, url, artist.lastfm_url
        )
        if u"Redirected from {}".format(name) in soup.text:  # TODO FIXME
            # may actually be fine, e.g. XXXTENTACION == XXXTentacion  FIXME
            self.log("Page redirects...", fg="red")
            continue_checks = False

        if continue_checks and self._wiki_is_disambiguation(artist, soup):
            soup = self._wiki_get_disambiguated_artist_soup(soup)
            if soup is None:
                continue_checks = False

        if continue_checks and self._wiki_is_artist_page(soup):
            return soup

        # Failed all wiki tries
        self._current_artist_stack[-1] = artist  # reset
        self.log(
            u"The URL scanned probably isn't a musician page... URL was {}"
            u"".format(url),
            fg='red'
        )

    def _lastfm_get_bio(self):
        """Lookup the artist on Last.fm and get bio from there."""
        if not self._lastfm_api_key:
            return None

        artist = self._current_artist_stack[-1]
        url = u"http://ws.audioscrobbler.com/2.0/"
        query = {
            'method': 'artist.getinfo',
            'artist': artist.name,
            'api_key': self._lastfm_api_key,
            'format': 'json'
        }
        self.log("Trying Last.FM...")
        req = requests.get(url, params=query)
        result_json = req.json()
        if result_json.get('error'):
            self.log(result_json['message'], fg='red')
            return None
        else:
            self._current_artist_stack[-1] = Artist(
                artist.name,
                artist.spotify_id,
                artist.wiki_url,
                result_json['artist']['url']
            )
            return result_json['artist']['bio']['content']

    def _wiki_is_group(self, soup):
        """Determine if this soup is an artist page..."""
        info_rows_texts = [th.text for th in self._wiki_get_info(soup)]
        try:
            members_ix = info_rows_texts.index('Members')
        except ValueError:
            return False
        return members_ix

    def _wiki_get_group_members(self, soup):
        """Get the group members - return list of Artists."""
        results = []
        info_rows = self._wiki_get_info(soup)
        info_rows_texts = [th.text for th in info_rows]
        members_ix = info_rows_texts.index('Members')
        for member in info_rows[members_ix].parent.find('td').find_all('li'):
            name = member.text
            link = member.find('a')
            if link:
                url = u"https://en.wikipedia.org{}".format(link['href'])
            else:
                url = None
            results.append(Artist(
                name=name, spotify_id=None, wiki_url=url, lastfm_url=None
            ))
        return results

    def _wiki_get_group_genders(self, artist_soup):
        """Get the genders of all the group members."""
        if len(self._current_artist_stack) > 1:
            self.log("Bailing - too many groups deep.", fg="red")
            return None, []

        lead = None
        names = []
        genders = []
        for ix, artist in enumerate(self._wiki_get_group_members(artist_soup)):
            gender = self.genderise(artist)
            names.append(artist.name)
            genders.append(gender)
            if ix == 0:
                lead = gender
        gender_counts = Counter(genders)
        members = MemberResults(
            gender_counts['nonbinary'],
            gender_counts['female'],
            gender_counts['male'],
            gender_counts[None],
            ", ".join(names)
        )
        return lead, members

    def _wiki_get_bio(self, soup):
        """Return the interesting part of a wiki aritst page soup."""
        paras = soup.find_all('p')
        paras_text = ' '.join([p.get_text() for p in paras])
        return paras_text

    def _get_gender_and_context(self, corpus):
        """Parse corpus for a person."""
        gender = None
        context = None
        first_pronoun = None
        words = corpus.split(' ')
        for ix, word in enumerate(words):
            word = word.lower()
            word = re.sub(r'[^\w]', '', word)
            if word in [
                'his', 'her', 'their', 'he', 'she', 'they', 'them', 'him'
            ]:
                first_pronoun = word
                context = " ".join(words[ix - 5:ix + 5])
                break
        gender = PRONOUN_MAP.get(first_pronoun, None)
        return gender, context

    def store(self, artist, gender=None, context=None, is_group=False,
              lead=None, members=None):
        """Store the result in the database, and tell us about it!"""
        members = members or MemberResults(0, 0, 0, 0, "")
        row = (
            artist.name,
            artist.spotify_id,
            artist.wiki_url,
            artist.lastfm_url,
            context,
            gender,
            is_group,
            lead,
            members.nonbinary,
            members.female,
            members.male,
            members.unknown,
            members.names
        )
        self.show_log_line(
            artist,
            context,
            gender,
            is_group,
            lead,
            members
        )
        self._store_artist(row)

    def show_log_line(self, artist, context, gender, is_group, lead, members):
        """Turn a result into a printable thing."""
        if is_group:
            led = "{}-led".format(lead if lead else "unknown")
            a_type = "group"
            split = "({} non-binary, {} female, {} male, {} unknown)".format(
                members.nonbinary,
                members.female,
                members.male,
                members.unknown
            )
            member_names = members.names
            gender = ""
        else:
            led = ""
            a_type = "person"
            split = ""
            member_names = ""
            if gender:
                context = u"(from \"{}\")".format(context)
            else:
                gender = "gender-unknown"
                context = ""

        self.log(
            u"{} is a ".format(artist.name) +
            u" ".join([part for part in [
                    gender, led, a_type, split, member_names, context
                ] if part]),
            fg="green"
        )
        if artist.wiki_url:
            self.log(artist.wiki_url)
        if artist.lastfm_url:
            self.log(artist.lastfm_url)

    def set_artist_batch_from_spotify(self, offset=None):  # nocov
        """Get a batch of artists from Spotify API - set as to process."""
        self._fetched_artists_to_process = []
        if offset is None:
            offset = self._get_offset()

        self.log("Starting at offset = {}".format(offset), fg='blue')
        url = "https://api.spotify.com/v1/search"
        query = {
            'q': 'year:0000-9999',
            'type': 'artist',
            'limit': min([50, self._batch_limit]),
            'offset': offset,
        }
        headers = {
            'Authorization': "Bearer {}".format(self._spotify_token),
            'Accept': 'application/json',
            'Content-Type': 'application/json',
        }
        req = requests.get(url, params=query, headers=headers)
        resp = req.json()
        try:
            artists_json = resp['artists']['items']
            for artist in artists_json:
                name = artist['name']
                id_ = artist['id']
                self._fetched_artists_to_process.append(
                    Artist(
                        name=name, spotify_id=id_, wiki_url=None,
                        lastfm_url=None
                    )
                )
        except KeyError:
            try:
                error = resp['error']['message']
                raise RuntimeError(error)
            except KeyError:
                raise RuntimeError("Response was weird: {}".format(resp))

    def genderise_batch(self):
        """Just start genderising the batch."""
        offset = self._get_offset()
        for ix, artist in enumerate(self._fetched_artists_to_process):
            try:
                self.genderise(artist)
            except (KeyboardInterrupt, SystemExit):
                ix -= 1  # set back one for 'finally' to keep us here next go
                raise
            finally:
                self._set_offset(offset + ix + 1)

    def genderise(self, artist):
        """Get the gender of the artist name."""
        self.log('--------------------------------')
        name = artist.name
        result = self._checked_result(name)
        if result:
            if result.gender is None and not result.is_group:
                self.log(
                    u"Found {} in database, but unknown gender...".format(name)
                )
                if not self._delete_artist(name):
                    return
            else:
                self.log(u"Found {} in database.".format(name))
                self.show_log_line(*result)
                return

        gender = None
        lead = None
        members = []
        self.log(u'Trying to get gender(s) for {}...'.format(name))

        self._current_artist_stack.append(artist)
        artist_soup = self._wiki_get_artist_soup()
        if artist_soup:
            if self._wiki_is_group(artist_soup):
                lead, members = self._wiki_get_group_genders(artist_soup)
                self.store(
                    self._current_artist_stack[-1],
                    is_group=True, lead=lead, members=members
                )
            else:
                corpus = self._wiki_get_bio(artist_soup)
                gender, context = self._get_gender_and_context(corpus)
                self.store(
                    self._current_artist_stack[-1],
                    gender=gender, context=context
                )
        else:
            lastfm_bio = self._lastfm_get_bio()
            if lastfm_bio:
                gender, context = self._get_gender_and_context(lastfm_bio)
                self.store(
                    self._current_artist_stack[-1],
                    gender=gender, context=context
                )
        self._current_artist_stack.pop()
        return gender
