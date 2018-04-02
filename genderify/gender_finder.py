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
                 db_file_path=None, force_fetch=False):
        """Setup."""
        self._db_file_path = db_file_path or '.genderify.db'
        self._conn = None
        self._did_check_db_existing = False
        self._fetched_artists_to_process = []
        self._current_artist_stack = []
        self._spotify_token = spotify_token
        self._playlist_name = None
        self._playlist_description = None
        self._batch_limit = batch_limit
        self._lastfm_api_key = lastfm_api_key
        self._force_fetch = force_fetch
        self._report = {
            'artists': set(),
            'nonbinary': 0,
            'female': 0,
            'male': 0,
            'unknown': 0,
        }

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

    @property
    def playlist_name(self):
        """Just return the playlist name if we got it..."""
        return self._playlist_name

    @property
    def playlist_description(self):
        """Just return the playlist description if we got it..."""
        return self._playlist_description

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

    def _get_headers(self, extra_headers=None):
        """Get UA headers, and add any extras."""
        extra_headers = extra_headers or {}
        headers = {
            'User-Agent':
            'Genderify 0.1 - http://github.com/stringfellow/genderify',
        }
        headers.update(extra_headers)
        return headers

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
        if is_artist:
            self.log("This appears to be an artist page.")
        else:
            self.log("This isn't an artist page...")

        return is_artist

    def _wiki_is_disambiguation(self, soup):
        """Return True if this a disambiguation page."""
        artist = self._current_artist_stack[-1]
        name = artist.name.lower()
        text = soup.text.lower()
        if u"{} may refer to:".format(name) in text:
            return True
        if u"{} may also refer to:".format(name) in text:
            return True
        if u"{} can refer to:".format(name) in text:
            return True
        self.log("Not a disambiguation page...")
        return False

    def _wiki_get_disambiguated_artist_soup(self, soup):
        """Get the actual soup from the disambiguation page."""
        artist = self._current_artist_stack[-1]
        links = [
            link for link in soup.find_all('a')
            if 'band' in link.text.lower()  # TODO: use regex \b for better
        ]
        if len(links) > 1:
            self.log(
                u"Too many choices!\n * {}".format(
                    u"\n * ".join([
                        link.text for link in links
                    ])
                ),
                fg="red"
            )
        elif len(links) == 1:
            url = u"https://en.wikipedia.org{}".format(links[0]['href'])
            self.log(u"Trying Wikipedia URL {}...".format(url))
            req = requests.get(url)
            text = req.text
            soup = BeautifulSoup(text, "html.parser")
            if self._wiki_is_artist_page(soup):
                self._current_artist_stack[-1] = Artist(
                    artist.name, artist.spotify_id, url, artist.lastfm_url
                )
                return soup
        return None  # TODO FIXME

    def _wiki_find_disambiguation(self, soup):
        """Try and find the page that is the disambiguation."""
        artist = self._current_artist_stack[-1]
        if u"For other uses, see {}".format(artist.name) in soup.text:
            # now find the link with (disambiguation) after it...?
            links = [
                link for link in soup.find_all('a')
                if link.text == u"{} (disambiguation)".format(artist.name)
            ]
            if len(links):
                url = u"https://en.wikipedia.org{}".format(links[0]['href'])
                self.log(u"Trying Wikipedia URL {}...".format(url))
                req = requests.get(url)
                text = req.text
                soup = BeautifulSoup(text, "html.parser")
                if self._wiki_is_disambiguation(soup):
                    soup = self._wiki_get_disambiguated_artist_soup(soup)
                    return soup
                self.log(u"Can't disambiguate at {}".format(url), fg="red")

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

        if continue_checks and self._wiki_is_disambiguation(soup):
            soup = self._wiki_get_disambiguated_artist_soup(soup)
            if soup is None:
                self.log(u"Can't disambiguate at {}".format(url), fg="red")
                continue_checks = False

        if continue_checks:
            is_artist_page = self._wiki_is_artist_page(soup)
            if is_artist_page:
                return soup
            else:  # try some tricks
                old_soup = soup  # to restore state later...
                soup = self._wiki_find_disambiguation(soup)
                if soup is None:
                    soup = old_soup
                # well, fail here... what else can we try?

        # Failed all wiki tries
        self._current_artist_stack[-1] = artist  # reset
        self.log(
            u"The URL scanned probably isn't a musician page... URL was {}"
            u"".format(url),
            fg='red'
        )

    def _wiki_is_group(self, soup):
        """Determine if this soup is an artist page..."""
        info_rows_texts = [th.text for th in self._wiki_get_info(soup)]
        try:
            info_rows_texts.index('Members')
            self.log("This is a group - it has a members section")
            return True
        except ValueError:
            self.log("This is not a group - no members section")
            return False

    def _wiki_get_group_members(self, soup):
        """Get the group members - return list of Artists."""
        results = []
        info_rows = self._wiki_get_info(soup)
        info_rows_texts = [th.text for th in info_rows]
        members_ix = info_rows_texts.index('Members')
        cell = info_rows[members_ix].parent.find('td')
        members = cell.find_all('li')
        if not members:
            members = [
                el for el in list(cell.children)
                if unicode(el) not in (u'<br/>', u'\n')
            ]
        if not members:
            self.log("No group members found!", fg="red")
        for member in members:
            url = None
            try:
                name = member.text.strip()
                link = member.find('a')
                if link:
                    url = u"https://en.wikipedia.org{}".format(link['href'])
            except AttributeError:
                name = unicode(member).strip()
            results.append(Artist(
                name=name, spotify_id=None, wiki_url=url, lastfm_url=None
            ))
        return results

    def _wiki_get_bio(self, soup):
        """Return the interesting part of a wiki aritst page soup."""
        paras = soup.find_all('p')
        paras_text = ' '.join([p.get_text() for p in paras])
        return paras_text

    def _lastfm_get_artist_soup(self):
        """Try to get the artist page, few options to check..."""
        artist = self._current_artist_stack[-1]
        name = artist.name
        url = artist.lastfm_url or (
            u"https://www.last.fm/music/{}/+wiki".format(
                name.replace(' ', '+')
            )
        )
        self.log(u"Trying Last.FM URL {}...".format(url))
        req = requests.get(url)
        if req.status_code == 404:
            self.log("The artist was not found.")
            return None
        text = req.text
        soup = BeautifulSoup(text, "html.parser")

        self._current_artist_stack[-1] = Artist(  # update with current url
            name, artist.spotify_id, artist.wiki_url, url
        )
        return soup

    def _lastfm_get_info(self, soup):
        """Get the 'factbox' rows from the RHS of lastfm wiki page."""
        info_rows = soup.select('li.factbox-item')
        return info_rows

    def _lastfm_is_group(self, soup):
        """Determine if this soup is an artist page..."""
        info_rows_texts = [
            li.find('h4').text for li in self._lastfm_get_info(soup)
        ]
        try:
            info_rows_texts.index('Members')
            self.log("This is a group - it has a members section")
            return True
        except ValueError:
            self.log("This is not a group - no members section")
            return False

    def _lastfm_get_group_members(self, soup):
        """Get the group members - return list of Artists."""
        results = []
        info_rows = self._lastfm_get_info(soup)
        info_rows_texts = [
            li.find('h4').text for li in self._lastfm_get_info(soup)
        ]
        members_ix = info_rows_texts.index('Members')
        ul = info_rows[members_ix].find('ul')
        members = ul.find_all('li')
        if not members:
            self.log("No group members found!", fg="red")
        for member in members:
            url = None
            try:
                link = member.find('a')
                name = link.text.strip()
                if link:
                    url = u"https://www.last.fm{}/+wiki".format(link['href'])
            except AttributeError:
                name = member.find('span').text.strip()
            results.append(Artist(
                name=name, spotify_id=None, wiki_url=None, lastfm_url=url
            ))
        return results

    def _lastfm_get_bio(self, soup):
        """Return the interesting part of a wiki aritst page soup."""
        paras = soup.select('div.wiki-content p')
        if paras is None:
            return None
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
        return DBRow(
                artist,
                context, gender, is_group, lead,
                members
            )

    def show_log_line(self, artist, context, gender, is_group, lead, members):
        """Turn a result into a printable thing."""
        if is_group:
            # Can't consistently tell 'leadership' so ignore.
            # led = "{}-led".format(lead if lead else "unknown")
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
            # led = ""
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
                    gender, a_type, split, member_names, context
                ] if part]),
            fg="green"
        )
        if artist.wiki_url:
            self.log(artist.wiki_url)
        if artist.lastfm_url:
            self.log(artist.lastfm_url)

    def set_artist_batch_from_spotify_search(self, offset=None):  # nocov
        """Get a batch of artists from Spotify search API, set to process."""
        self._fetched_artists_to_process = []
        if offset is None:
            offset = self._get_offset()
        else:
            self._set_offset(offset)

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
        req = requests.get(
            url, params=query, headers=self._get_headers(headers)
        )
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

    def set_artists_batch_from_spotify_public_playlist(
        self, url=None, user_id=None, playlist_id=None
    ):
        """Set the batch of artists to be from one public Spotify playlist."""
        if url is None and (user_id is None or playlist_id is None):
            raise ValueError(
                "Provide either playlist URL or user id AND playlist id."
            )

        if url is not None:
            # e.g.
            # https://open.spotify.com/user/stringfellow/playlist/2UcZJ3R9bSjSZ5czRAYKhJ?si=e_t9S7vPQiGSdjo3VXSzFA  noqa
            parts = url.split('/')
            user_id = parts[parts.index('user') + 1]
            playlist_id = parts[parts.index('playlist') + 1]
            if '?' in playlist_id:
                playlist_id = playlist_id.split('?')[0]

        url = (
            "https://api.spotify.com/v1/users/{user_id}"
            "/playlists/{playlist_id}"
        ).format(user_id=user_id, playlist_id=playlist_id)
        headers = {
            'Authorization': "Bearer {}".format(self._spotify_token),
            'Accept': 'application/json',
            'Content-Type': 'application/json',
        }
        req = requests.get(url, headers=self._get_headers(headers))
        resp = req.json()
        try:
            self._playlist_name = resp['name']
            self._playlist_description = resp['description']
            artist_set = set()
            for track in resp['tracks']['items']:
                artists = track['track']['artists']
                for artist in artists:
                    artist_set.add(
                        Artist(
                            name=artist['name'],
                            spotify_id=artist['id'],
                            wiki_url=None,
                            lastfm_url=None
                        )
                    )
            self._fetched_artists_to_process = list(artist_set)
        except KeyError:
            try:
                error = resp['error']['message']
                raise RuntimeError(error)
            except KeyError:
                raise RuntimeError("Response was weird: {}".format(resp))

    def add_to_report(self, dbrow):
        """Report on this result..."""
        artist, context, gender, is_group, lead_gender, members = dbrow
        if artist in self._report['artists']:
            return
        if not is_group:
            self._report[gender or "unknown"] += 1
        else:
            for _gender in ['nonbinary', 'female', 'male', 'unknown']:
                self._report[_gender] += getattr(members, _gender)

    def get_report(self):
        """Get report on batches processed this session."""
        unique_artists = len(self._report['artists'])
        report_str = ", ".join([
            "{} {} {}".format(
                self._report[gender],
                gender,
                "person" if self._report[gender] == 1 else "people"
            )
            for gender in ['nonbinary', 'female', 'male', 'unknown']
        ])
        print(
            "Of {} unique artists found, they are made up of "
            "{}".format(unique_artists, report_str)
        )
        return self._report

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

    def _get_artist_soup(self, source):
        """Defer to different source."""
        return getattr(self, '_{}_get_artist_soup'.format(source))()

    def _is_group(self, source, soup):
        """Defer to different source."""
        return getattr(self, '_{}_is_group'.format(source))(soup)

    def _get_bio(self, source, soup):
        """Defer to different source."""
        return getattr(
            self, '_{}_get_bio'.format(source)
        )(soup)

    def _genderise_from_source(self, source):
        """Try and get a result from a source."""
        result = None
        artist_soup = self._get_artist_soup(source)
        if artist_soup:
            is_group = self._is_group(source, artist_soup)
            if is_group:
                lead, members = self._get_group_genders(source, artist_soup)
                result = self.store(
                    self._current_artist_stack[-1],
                    is_group=True,
                    lead=lead,
                    members=members
                )
            else:
                corpus = self._get_bio(source, artist_soup)
                if corpus is None or not corpus.strip():
                    return None
                gender, context = self._get_gender_and_context(corpus)
                result = self.store(
                    self._current_artist_stack[-1],
                    gender=gender,
                    context=context
                )
        return result

    def _get_group_genders(self, source, soup):
        """Get the genders of all the group members."""
        if len(self._current_artist_stack) > 1:
            self.log("Bailing - too many groups deep.", fg="red")
            return None, []

        lead = None
        names = []
        genders = []
        fn = getattr(self, '_{}_get_group_members'.format(source))
        for ix, artist in enumerate(fn(soup)):
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

    def genderise(self, artist):
        """Get the gender of the artist name."""
        self.log(
            '--------------- {} -----------------'.format(self._get_offset())
        )
        name = artist.name
        result = self._checked_result(name)
        if result:
            if result.gender is None and not result.is_group:
                self.log(
                    u"Found {} in database, but unknown gender...".format(
                        name
                    ), fg="blue"
                )
                if not self._delete_artist(name):
                    self.log(
                        "Couldn't delete old record... skipping.", fg="red"
                    )
                    return
            elif self._force_fetch:
                self.log(
                    u"Found {} in database, but forcing a re-fetch".format(
                        name
                    ), fg="blue"
                )
                if not self._delete_artist(name):
                    self.log(
                        "Couldn't delete old record... skipping.", fg="red"
                    )
                    return
            else:
                self.log(u"Found {} in database.".format(name))
                self.add_to_report(result)
                self.show_log_line(*result)
                return

        gender = None
        self.log(u'Trying to get gender(s) for {}...'.format(name))

        self._current_artist_stack.append(artist)
        sources = ['wiki', 'lastfm']
        result = None
        while len(sources) and result is None:
            result = self._genderise_from_source(sources.pop())

        if result is not None:
            self.add_to_report(result)
            gender = result.gender
        else:
            self.add_to_report(
                DBRow(artist, '', None, False, '', None)
            )
            self.log(
                u"Couldn't find a gender for {}".format(artist.name), fg="red"
            )
        self._current_artist_stack.pop()
        return gender


class GenderifierLastFMAPI(Genderifier):
    """Dud bits from the lastfm API which doesn't give rich info."""

    def _lastfm_is_group_from_api(self, bio):
        """Try and see if this is a group.. hard..."""
        artist = self._current_artist_stack[-1]
        # some dodgy shortcuts...
        if ' orchestra' in artist.name.lower():
            self.log("This is a group - name contains ' Orchestra'")
            return True
        if ' band' in artist.name.lower():
            self.log("This is a group - name contains ' Band'")
            return True

        bio_words = bio.lower().split()
        first_pronoun_index = 9999999
        pronouns = ['her', 'she', 'his', 'him', 'he']
        for pronoun in pronouns:
            try:
                p_ix = bio_words.index(pronoun)
                first_pronoun_index = min([first_pronoun_index, p_ix])
            except ValueError:
                continue

        first_group_index = 9999999
        group_indicators = ['group', 'band', 'orchestra']
        for group_word in group_indicators:
            try:
                g_ix = bio_words.index(group_word)
                first_group_index = min([first_group_index, g_ix])
            except ValueError:
                continue

        if first_group_index < first_pronoun_index:
            return True

    def _lastfm_get_bio_via_api(self):
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
        req = requests.get(url, params=query, headers=self._get_headers())
        result_json = req.json()
        if result_json.get('error'):
            self.log(result_json['message'], fg='red')
            return None
        self._current_artist_stack[-1] = Artist(
            artist.name,
            artist.spotify_id,
            artist.wiki_url,
            result_json['artist']['url']
        )
        return result_json['artist']['bio']['content']
