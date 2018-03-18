# Genderify

## Purpose

To discover the potential bias toward a particular gender of musical artists
amongst a person's listening. Using Spotify as a source of artists, and of a
person's music library, playlists etc, the biographies (found on either
wikipedia or last.fm) of the artists are inspected to see if it is possible to
determine a gender - or genders in the case of a group, which for the purposes
of this exercise are limited to:

 * Non-binary
 * Female 
 * Male

... though the authors respect and appreciate this is an over simplification.

## Caveats

There are so many, I'm sure, but here are a few very obvious ones:
* *This work does not serve as a body of facts nor opinions,
  it is a simplistic algorithmic determination of gender based on the English
  language, without the nuances of time, intuition, fact-checking, personal
  communications or anything else that might make it official in any way. It's
  a dumb robot.*
* We can't always determine gender from a person by their bio - they may
  privately identify as one thing while publicly identifying another way, for
  personal reasons.
* 'Gender' of a band or group is difficult, so we've opted to just represent
  the split of that group, and determine the gender of the 'front person'
  (which is the person at the top of the list in the 'Members' section on
  Wikipedia). 
* Groups or artists with the same name will probably confuse this dumb script.
  It will only store whichever one it encounters first.
* Because it's quite hard to get a list of "all the artists in the world"
  (is it?) we are just using Spotify's search API which gives an opaquely
  sorted list of artists back with each call - so, we probably miss a lot.

## Conflict

Having just finished reading Cordelia Fine's
"Delusions of Gender"<sup>[1](#delusionsofgender)</sup>, I (Steve)
feel a bit conflicted about this exercise, since it is adhering to a divide
which I do not believe should be reinforced, and it seems that explicitly 
drawing out the genders of artists is doing exactly that (particularly with 
respect to those many many identities which we've dumped into a single 
"non-binary").

On the other hand, with a feminist hat on, it feels more important that while
the social divide *does* exist, we ought to do everything we can to redress the
balance. So, we offer this piece of work to attempt to lift women and non-binary
people out of the shadow of men.


<a name="delusionsofgender">1</a>: A must read. [Delusions of Gender, Cordelia Fine, 2010 (W. W. Norton & Company)](https://en.wikipedia.org/wiki/Delusions_of_Gender)
