#!/usr/bin/env python
# -*- coding: utf-8 -*-
from collections import Counter
import re
import sqlite3

from bs4 import BeautifulSoup
import click
import requests

CONN = sqlite3.connect('gendify.db')
DID_CHECK = False
PRONOUN_MAP = {
    'their': 'non-binary',
    'they': 'non-binary',
    'them': 'non-binary',
    'her': 'female',
    'she': 'female',
    'his': 'male',
    'him': 'male',
    'he': 'male',
}


def get_db():
    """Just setup the database and return a cursor."""
    global DID_CHECK
    curs = CONN.cursor()
    if not DID_CHECK:
        curs.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='meta'"
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
                    spotify_id TEXT,
                    url TEXT,
                    name TEXT,
                    gender TEST,
                    is_group BOOLEAN
                )
                """
            )
        DID_CHECK = True
    return curs


def _get_offset():
    """Get the last offset."""
    curs = get_db()
    curs.execute("SELECT offset FROM meta ORDER BY timestamp DESC LIMIT 1")
    offset = curs.fetchone() or 0
    return offset


def _store_artists(artist_tuples):
    """Store the results - (spotify_id, url, name, gender, is_group)."""
    curs = get_db()
    try:
        curs.executemany(
            """
            INSERT INTO artists(spotify_id, url, name, gender, is_group)
            VALUES (?, ?, ?, ?, ?)
            """,
            artist_tuples
        )
        CONN.commit()
    except sqlite3.ProgrammingError:
        import ipdb; ipdb.set_trace()
        print "error."


def _checked_result(url):
    """Check to see if we already got this."""
    curs = get_db()
    curs.execute(
        "SELECT name, gender, is_group FROM artists WHERE url = ?", (url,)
    )
    result = curs.fetchone()
    if result:
        click.echo(u'{} found in database! Skipping....'.format(result[0]))
        return result[1:]


def _gender_person(soup, name, url):
    """Parse wikipedia for single artist."""
    paras = soup.find_all('p')
    paras_text = ' '.join([p.get_text() for p in paras])

    corpus = paras_text.split(' ')

    first_pronoun = None
    for ix, word in enumerate(corpus):
        word = word.lower()
        word = re.sub(r'[^\w]', '', word)
        if word in [
            'his', 'her', 'their', 'he', 'she', 'they', 'them', 'him'
        ]:
            first_pronoun = word
            context = " ".join(corpus[ix - 5:ix + 5])
            break
    gender = PRONOUN_MAP.get(first_pronoun, False)
    if gender:
        click.secho(
            u"{} probably identifies as {} based on first pronoun in "
            u"context being \"{}\"".format(
                name, gender, context
            ), fg='green'
        )
    return gender


def _gender_group(info_rows, name, url, spotify_id=None, inset=0):
    """Gender a group -- if ValueError raised, then not a group."""
    results = []
    info_rows_texts = [th.text for th in info_rows]
    members_ix = info_rows_texts.index('Members')
    members = [
        (member.text, member['href'])
        for member in
        info_rows[members_ix].parent.find('td').find_all('a')
    ]
    member_results = []
    for member in members:
        do_store, member_result = genderise(*member, inset=inset + 1)
        if member_result:
            member_results.extend(member_result)
            if do_store:
                results.append(member_result)

    member_genders = [g[3] for g in member_results]
    genders = list(set(member_genders))
    if len(genders) == 1:
        gender = genders[0]
    elif len(genders):  # non-trinary!
        counter = Counter(member_genders)
        led = '{}-led'.format(member_genders[0])
        pct = []
        total = float(sum(counter.values()))
        for _gender, count in counter.items():
            pct.append('{:0.0f}% {}'.format(count / total * 100, _gender))
        gender = "{} and split: {}".format(led, ", ".join(pct))
    else:
        gender = False

    if gender:
        click.secho(
            "{}{} is a group and is {}".format(' ' * inset, name, gender),
            fg='green'
        )

        results.append((spotify_id, url, name, gender, True))
    return results


def genderise(name, uri=None, spotify_id=None, inset=0):
    """Get the gender of the artist name."""
    if uri is not None:
        url = u"https://en.wikipedia.org{}".format(uri)
    else:
        url = u"https://en.wikipedia.org/wiki/{}".format(
            name.title().replace(' ', '_')
        )

    results = []
    result = _checked_result(url)
    if result:
        return False, [result]

    click.echo(url)
    req = requests.get(url)
    text = req.text
    soup = BeautifulSoup(text, "html.parser")
    info_rows = soup.select('table.infobox tr th[scope="row"]')
    info_rows_texts = [th.text for th in info_rows]
    try:
        info_rows_texts.index('Labels')
    except ValueError:
        click.secho(
            "The URL scanned probably isn't a musician page, as there are no "
            "record labels... URL was {}".format(url), fg='red'
        )
        return False, []

    is_group = False
    try:
        group_results = _gender_group(
            info_rows, name, url, spotify_id=spotify_id, inset=inset
        )
        results.extend(group_results)
        is_group = True
        gender = len(results) > 0
    except ValueError:
        gender = _gender_person(soup, name, url)
        if gender:
            results.append((spotify_id, url, name, gender, is_group))

    if not gender:
        click.secho(
            u"Couldn't find gender for {}, check the URL: {}".format(
                name, url
            ), fg='red'
        )
    return True, results


@click.command()
@click.option(
    '--token', prompt="Enter OAuth token", help="Spotify OAuth token."
)
@click.option(
    '--name', help="Optionally limit to lookup this name.", default=None
)
@click.option(
    '--offset', help="Offset for fetching artist search results"
)
def gendify(token, name, offset=0):
    """Get all the artist names."""
    if name:
        do_store, results = genderise(name)
        if do_store:
            _store_artists(results)
    else:
        url = "https://api.spotify.com/v1/search"
        query = {
            'q': 'year:0000-9999',
            'type': 'artist',
            'limit': 50,
            'offset': offset
        }
        headers = {
            'Authorization': "Bearer {}".format(token),
            'Accept': 'application/json',
            'Content-Type': 'application/json',
        }
        req = requests.get(url, params=query, headers=headers)
        resp = req.json()
        try:
            artists_json = resp['artists']['items']
            artists_tuples = []
            for artist in artists_json:
                name = artist['name']
                id_ = artist['id']
                do_store, artist_results = genderise(name, spotify_id=id_)
                if do_store:
                    artists_tuples.extend(artist_results)
            _store_artists(artists_tuples)
        except KeyError:
            click.secho("Response was weird: {}".fomrat(resp), fg='red')


if __name__ == '__main__':
    gendify()
    CONN.close()
