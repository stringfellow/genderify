# -*- coding: utf-8 -*-
import click

from genderify.gender_finder import Genderifier


@click.command()
@click.option(
    '--spotify-token', help="Spotify OAuth token."
)
@click.option(
    '--lastfm-key', help="Spotify OAuth token."
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
    '--db-file-path', help="Path to db file.", default=None, type=click.Path()
)
@click.option(
    '--forever/--once', help="Keep going until killed, or just once.",
    default=False
)
def genderify(spotify_token, lastfm_key, name, offset, batch_limit,
              db_file_path, forever):
    """Get all the artist names."""

    with Genderifier(
        spotify_token=spotify_token,
        lastfm_api_key=lastfm_key,
        batch_limit=batch_limit,
        db_file_path=db_file_path,
    ) as genderifier:
        if name:
            genderifier.genderise(
                genderifier.get_artist_obj_from_name(name)
            )
            return

        while forever:
            genderifier.set_artist_batch_from_spotify(offset)
            genderifier.genderise_batch()
        else:
            genderifier.set_artist_batch_from_spotify(offset)
            genderifier.genderise_batch()


if __name__ == '__main__':
    genderify()
