#!/usr/bin/env python3

import argparse
import locale
import logging
import getpass
from plexapi.exceptions import BadRequest, NotFound
from plexapi.myplex import MyPlexAccount
import sys
import time

from MediaPlayer import MediaMonkey, PlexPlayer


class PlexSync:
	log_levels = {
		'CRITICAL': 50,
		'ERROR': 40,
		'WARNING': 30,
		'INFO': 20,
		'DEBUG': 10
	}

	def __init__(self, options):
		self.options = options
		self.setup_logging()
		self.local_player = self.get_player()
		self.remote_player = PlexPlayer()

	def both_albums_empty(self, local, remote):
		return self.local_player.album_empty(local) and self.remote_player.album_empty(remote)

	def albums_matching(self, local, remote):
		remote = remote.lower()
		local = local.lower()
		return remote == local or self.both_albums_empty(local, remote)

	def connect_to_plex(self):
		print(
			'Connecting to the plex server "{}" with username "{}"'.format(self.options.server, self.options.username))
		account = None
		plex = None
		tries_left = 3
		while tries_left > 0:
			time.sleep(1)  # important. Otherwise, the above print statement can be flushed after
			password = getpass.getpass()
			try:
				account = MyPlexAccount(self.options.username, password)
				break
			except NotFound:
				print('Username or password wrong'.format(self.options.server))
				tries_left -= 1
			except BadRequest as error:
				print(error)
				tries_left -= 1
		if tries_left == 0 or account is None:
			exit(1)

		try:
			plex = account.resource(self.options.server).connect(timeout=5)
			print('Success: Connected to plex server {}'.format(self.options.server))
		except NotFound:
			# This also happens if the user is not the owner of the server
			print('Error: Unable to connect to the server {}'.format(self.options.server))
			exit(1)

		return plex

	def get_player(self):
		supported_players = [MediaMonkey()]
		for player in supported_players:
			if player.name.lower() == self.options.player.lower():
				return player

		print('Unsupported media player: {}'.format(self.options.player), file=sys.stderr)
		print('Valid players: {}'.format(', '.join([player.name for player in supported_players])))
		exit(1)

	def setup_logging(self):
		logging.basicConfig()
		self.logger = logging.getLogger('plexsync')
		level = -1
		if isinstance(self.options.log, str):
			try:
				level = self.log_levels[self.options.log.upper()]
			except KeyError:
				pass
		elif isinstance(self.options.log, int):
			if 0 <= self.options.log <= 50:
				level = self.options.log

		if level < 0:
			self.invalid_logging_level_selected(self.options.log)
		else:
			self.logger.setLevel(level)

	def invalid_logging_level_selected(self, level):
		print('Valid logging levels specified by either key or value:{}'.format('\n\t'.join(
			'{}: {}'.format(key, value) for key, value in self.log_levels.items()))
		)
		raise RuntimeError('Invalid logging level selected: {}'.format(level))

	def sync(self):
		tracks = self.local_player.read_tracks()
		plex = self.connect_to_plex()
		library = self.select_music_library(plex)
		self.update_ratings(tracks, library)

	def update_ratings(self, tracks, library):
		for local_track in tracks:
			self.logger.debug('Searching for track "{}"'.format(local_track))
			remote_track = None

			artists = library.searchArtists(title=local_track.artist)
			for artist in artists:
				for _remote_track in artist.tracks(title=local_track.title):
					if self.albums_matching(local_track.album, _remote_track.album().title):
						remote_track = _remote_track
						break
					else:
						self.logger.debug('did not match "{} - {} - {}"'.format(
							_remote_track.artist().title, _remote_track.album().title, _remote_track.title
						))
				if remote_track is not None:
					break

			if remote_track is None:
				self.logger.warning('No match found for track "{}", skipping ...'.format(local_track))
				continue

			if 'userRating' in remote_track._data.attrib:
				remote_track.rating = self.remote_player.get_normed_rating(
					float(remote_track._data.attrib['userRating'])
				)
			else:
				remote_track.rating = 0

			if remote_track.rating > 0 and remote_track.rating != local_track.rating:
				# TODO: deal with this conflict later
				print('Found a mismatching rating for "{}":'.format(local_track))
				print(u'\t local: {:0.1f}☆'.format(self.local_player.get_5star_rating(local_track.rating)))
				print(u'\tremote: {:0.1f}☆'.format(self.remote_player.get_5star_rating(remote_track.rating)))
				choice = input('Override remote rating? [Yn]') or 'y'
				if choice.lower() != 'y':
					continue
			elif remote_track.rating == local_track.rating:
				self.logger.debug('already up to date')
				continue

			remote_track.edit(**{'userRating.value': self.remote_player.get_native_rating(local_track.rating)})
			self.logger.debug(
				u'Updated rating to {:0.1f} stars'.format(self.remote_player.get_5star_rating(local_track.rating)))

	def select_music_library(self, plex):
		type_music = 'artist'
		music_libraries = {section.key: section for section in plex.library.sections() if section.type == type_music}
		if len(music_libraries) == 0:
			raise RuntimeError('No music library found')
		elif len(music_libraries) == 1:
			return list(music_libraries.values())[0]
		else:
			print('Found multiple music libraries:')
			for key, library in music_libraries.items():
				print('\t[{}]: {}'.format(key, library.title))

			choice = input('Select the library to sync with: ')
			return music_libraries[choice]


def parse_args():
	parser = argparse.ArgumentParser(description='Synchronizes ID3 music ratings with a Plex media-server')
	parser.add_argument('--server', type=str, required=True, help='The name of the plex media server')
	parser.add_argument('--username', type=str, required=True, help='The plex username')
	parser.add_argument('--player', type=str, required=True, help='Media player to synchronize with Plex')
	parser.add_argument('--log', default='warning', help='Sets the logging level')
	return parser.parse_args()


if __name__ == "__main__":
	locale.setlocale(locale.LC_ALL, '')
	args = parse_args()
	sync_agent = PlexSync(args)
	sync_agent.sync()
