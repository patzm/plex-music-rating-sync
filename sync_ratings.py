import argparse
import locale
import getpass
from plexapi.exceptions import BadRequest, NotFound
from plexapi.myplex import MyPlexAccount
from dialog import Dialog
import sys
import time

from MediaPlayer import MediaMonkey, PlexPlayer


def main(options):
	player = get_player(options.player)
	tracks = player.read_tracks()
	plex = connect_to_plex(options)
	library = select_music_library(plex)
	match_tracks(tracks, library)


def connect_to_plex(options):
	print('Connecting to the plex server "{}" with username "{}"'.format(options.server, options.username))
	account = None
	plex = None
	tries_left = 3
	while tries_left > 0:
		time.sleep(1) # important. Otherwise, the above print statement can be flushed after
		password = getpass.getpass()
		try:
			account = MyPlexAccount(options.username, password)
			break
		except NotFound:
			print('Username or password wrong'.format(options.server))
			tries_left -= 1
		except BadRequest as error:
			print(error)
			tries_left -= 1
	if tries_left == 0 or account is None:
		exit(1)

	try:
		plex = account.resource(options.server).connect(timeout=5)
		print('Success: Connected to plex server {}'.format(options.server))
	except NotFound:
		# This also happens if the user is not the owner of the server
		print('Error: Unable to connect to the server {}'.format(options.server))
		exit(1)

	return plex


def get_player(value):
	supported_players = [MediaMonkey()]
	for player in supported_players:
		if player.name.lower() == value.lower():
			return player

	print('Unsupported media player: {}'.format(value), file=sys.stderr)
	print('Valid players: {}'.format(', '.join([player.name for player in supported_players])))
	exit(1)


def match_tracks(tracks, library):
	player = PlexPlayer()
	for track in tracks:
		print('Searching for track "{}"'.format(track))
		match = None
		for result in library.searchTracks(title=track.title.lower()):
			if result.title == track.title and result.artist().title == track.artist and result.album().title == track.album:
				match = result
				if 'userRating' in match._data.attrib:
					match.rating = float(match._data.attrib['userRating'])
				else:
					match.rating = 0
				break

		if match is None:
			print('Warning: no match found for track "{}"'.format(track), file=sys.stderr)
		else:
			print('Found match with rating {}'.format(player.get_5star_rating(player.get_normed_rating(match.rating))))


def select_music_library(plex):
	type_music = 'artist'
	music_libraries = {section.key: section for section in plex.library.sections() if section.type == type_music}
	if len(music_libraries) == 0:
		raise RuntimeError('No music library found')
	elif len(music_libraries) == 1:
		return list(music_libraries.values())[0]
	else:
		d = Dialog(dialog="Library selection")
		code, selection = d.menu("Select the plex server library to sync with:",
		                         choices=[(key, library.title) for key, library in music_libraries.items()]
		                         )
		if code == d.OK:
			return music_libraries[selection]


def parse_args():
	parser = argparse.ArgumentParser(description='Synchronizes ID3 music ratings with a Plex media-server')
	parser.add_argument('--server', type=str, help='The name of the plex media server')
	parser.add_argument('--username', type=str, help='The plex username')
	parser.add_argument('--player', type=str, help='Media player to synchronize with Plex')
	return parser.parse_args()


if __name__ == "__main__":
	locale.setlocale(locale.LC_ALL, '')
	args = parse_args()
	main(args)
