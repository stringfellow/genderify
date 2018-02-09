#!/usr/bin/env python
# -*- coding: utf-8 -*-
from collections import Counter
import re
import sqlite3

from bs4 import BeautifulSoup
import click
import requests

CONN = sqlite3.connect('.genderify.db')
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


def _set_offset(offset):
    """Set the offset."""
    curs = get_db()
    curs.execute("INSERT INTO meta(offset) VALUES (?)", (offset,))
    CONN.commit()


def _get_offset():
    """Get the last offset."""
    curs = get_db()
    curs.execute("SELECT offset FROM meta ORDER BY timestamp DESC LIMIT 1")
    offset = curs.fetchone()
    return offset[0] if offset else 0


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
        return True
    except sqlite3.ProgrammingError as err:
        click.secho(err, fg='red')


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
                results.extend(member_result)

    member_genders = [g[3] for g in member_results]
    genders = list(set(member_genders))
    if len(genders) == 1:
        gender = 'all-{}'.format(genders[0])
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


def _get_artist_page(name, uri=None):
    """Try to get the artist page, few options to check..."""
    # TODO: Only tries first thing, should try others.
    url = u"https://en.wikipedia.org{}".format(
        uri if uri else u'/wiki/{}'.format(name.title().replace(' ', '_'))
    )
    scrape_it = False
    result = _checked_result(url)
    if result:
        return scrape_it, None, None, url, [result]

    click.echo(url)
    req = requests.get(url)
    text = req.text
    soup = BeautifulSoup(text, "html.parser")
    info_rows = soup.select('table.infobox tr th[scope="row"]')
    info_rows_texts = [th.text for th in info_rows]
    scrape_it = set(['Genres', 'Labels', 'Instruments']) & set(info_rows_texts)
    if not scrape_it:
        click.secho(
            "The URL scanned probably isn't a musician page, as there are"
            " no genres or record labels... URL was {}".format(url),
            fg='red'
        )
    return scrape_it, soup, info_rows, url, None


def genderise(name, uri=None, spotify_id=None, inset=0):
    """Get the gender of the artist name."""
    results = []

    scrape_it, soup, info_rows, url, maybe_results = _get_artist_page(
        name, uri
    )
    if url and not scrape_it:
        return False, maybe_results
    elif not scrape_it:
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


def _from_spotify(token, offset, batch_limit, forever):
    """Get results from spotify."""
    if offset is None:
        offset = _get_offset()

    click.secho("Starting at offset = {}".format(offset), fg='blue')
    url = "https://api.spotify.com/v1/search"
    query = {
        'q': 'year:0000-9999',
        'type': 'artist',
        'limit': min([50, batch_limit]),
        'offset': offset,
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

        if _store_artists(artists_tuples):
            offset = resp['artists']['limit'] + resp['artists']['offset']
            _set_offset(offset)
            if forever:
                _from_spotify(token, offset, batch_limit, forever)
    except KeyError:
        click.secho("Response was weird: {}".format(resp), fg='red')
    except KeyboardInterrupt:
        click.secho("You stopped it!", fg='blue')


@click.command()
@click.option(
    '--token', prompt="Enter OAuth token", help="Spotify OAuth token."
)
@click.option(
    '--name', help="Optionally limit to lookup this name.", default=None
)
@click.option(
    '--offset', help="Offset for fetching artist search results", default=None,
    type=int
)
@click.option(
    '--batch-limit', help="How many to fetch at once", default=50,
    type=int
)
@click.option(
    '--forever/--once', help="Keep going until killed, or just once.",
    default=False
)
def gendify(token, name, offset, batch_limit, forever):
    """Get all the artist names."""
    if name:
        do_store, results = genderise(name)
        if do_store:
            _store_artists(results)
    else:
        _from_spotify(token, offset, batch_limit, forever)


if __name__ == '__main__':
    gendify()
    CONN.close()
