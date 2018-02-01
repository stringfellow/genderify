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

    corpus = paras_text.split(' ')

    for ix, word in enumerate(corpus):
        if word.lower() in [
            'his', 'her', 'their', 'he', 'she', 'they', 'them', 'him'
        ]:
            first_pronoun = word
            context = " ".join(corpus[ix - 5:ix + 5])
            break

    result = {
        'their': 'non-binary',
        'they': 'non-binary',
        'them': 'non-binary',
        'her': 'female',
        'she': 'female',
        'his': 'male',
        'him': 'male',
        'he': 'male',
    }[first_pronoun]
    print (
        "{} probably identifies as {} based on first pronoun in context "
        "being \"{}\"".format(
            name, result, context
        )
    )


if __name__ == '__main__':
    genderise()
