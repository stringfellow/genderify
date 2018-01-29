#!/usr/bin/env python
# -*- coding: utf-8 -*-
from collections import Counter

from bs4 import BeautifulSoup
import click
import requests

@click.command()
@click.option('--name', prompt='Artist name', help='The person to gender.')
def genderise(name):
    """Get the gender of the artist name."""
    url = "https://en.wikipedia.org/wiki/{}".format(
        name.title().replace(' ', '_')
    )
    print url
    req = requests.get(url)
    text = req.text.lower()
    soup = BeautifulSoup(text, "html.parser")
    paras = soup.find_all('p')
    paras_text = ' '.join([p.get_text() for p in paras])

    splits = [w for w in paras_text.split(' ') if w in ['she', 'he']]
    counter = Counter(splits)
    result = None
    if counter['she'] > counter['he']:
        result = 'female'
    elif counter['he'] > counter['she']:
        result = 'male'

    if result:
        print "{} is probably {}".format(name, result)
    else:
        print dict(counter)




if __name__ == '__main__':
    genderise()
