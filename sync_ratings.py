#!/usr/bin/env python3
				  
import configargparse
import locale
import logging
import sys

from sync_pair import *
from MediaPlayer import MediaPlayer, MediaMonkey, PlexPlayer

class InfoFilter(logging.Filter):
	def filter(self, rec):
		return rec.levelno in (logging.DEBUG, logging.INFO)

class PlexSync:
	log_levels = {
		'CRITICAL': logging.CRITICAL,
		'ERROR': logging.ERROR,
		'WARNING': logging.WARNING,
		'INFO': logging.INFO,
		'DEBUG': logging.DEBUG
	}

	def __init__(self, options):   
		self.logger = logging.getLogger('PlexSync')
		self.options = options
		self.setup_logging()
		self.local_player = self.get_player()
		self.remote_player = PlexPlayer()
		self.local_player.dry_run = self.remote_player.dry_run = self.options.dry
		self.local_player.reverse = self.remote_player.reverse = self.options.reverse
		self.local_player.full = self.remote_player.full = self.options.full

	def get_player(self):
		"""

		:rtype: MediaPlayer
		"""
		_player = self.options.player.lower()
		supported_players = {MediaMonkey.name()}
		if _player == MediaMonkey.name().lower():
			return MediaMonkey()
		else:
			self.logger.error('Valid players: {}'.format(', '.join(supported_players)))
			self.logger.error('Unsupported player selected: {}'.format(self.options.player))
			exit(1)

	def setup_logging(self):
		self.logger.setLevel(logging.DEBUG)

		# Set up the two formatters
		formatter_brief = logging.Formatter(fmt='[%(asctime)s] %(levelname)s: %(message)s', datefmt='%H:%M:%S')
		formatter_explicit = logging.Formatter(
			fmt='[%(asctime)s] %(levelname)s [%(name)s.%(funcName)s:%(lineno)d] %(message)s',
			datefmt='%H:%M:%S'
		)

		# Set up the file logger
		fh = logging.FileHandler(filename='sync_ratings.log', mode='w')
		fh.setLevel(logging.DEBUG)
		fh.setFormatter(formatter_explicit)
		self.logger.addHandler(fh)

		# Set up the error / warning command line logger
		ch_err = logging.StreamHandler(stream=sys.stderr)
		ch_err.setFormatter(formatter_explicit)
		ch_err.setLevel(logging.WARNING)
		self.logger.addHandler(ch_err)

		# Set up the verbose info / debug command line logger
		ch_std = logging.StreamHandler(stream=sys.stdout)
		ch_std.setFormatter(formatter_brief)
		ch_std.addFilter(InfoFilter())
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
			print('Valid logging levels specified by either key or value:{}'.format('\n\t'.join(
				'{}: {}'.format(key, value) for key, value in self.log_levels.items()))
			)
			raise RuntimeError('Invalid logging level selected: {}'.format(level))
		else:
			ch_std.setLevel(level)
			self.logger.addHandler(ch_std)

	def sync(self):
		self.local_player.connect()
		self.remote_player.connect(
			server=self.options.server,
			username=self.options.username,
			password=self.options.passwd,
			token=self.options.token
		)
		if self.options.reverse:
			source_name = self.remote_player.name()
			destination_name = self.local_player.name()
		else:
			source_name = self.local_player.name()
			destination_name = self.remote_player.name()
		for sync_item in self.options.sync:
			if sync_item.lower() == "tracks":
				self.logger.info('Starting to sync track ratings from {} to {}'.format(source_name, destination_name))
				self.sync_tracks()
			elif sync_item.lower() == "playlists":
				#TODO: finish implementing playlist sync for MediaMonkey -> Plex
				self.logger.info('Starting to sync playlists from {} to {}'.format(source_name, destination_name))
				if not self.options.reverse:
					self.sync_playlists()
			else:
				raise ValueError('Invalid sync item selected: {}'.format(sync_item))			
        
	def sync_tracks(self):
		if self.options.reverse:
			tracks = self.remote_player.search_tracks(rating=True)
			self.logger.info('Attempting to match {} tracks'.format(len(tracks)))
			sync_pairs = [TrackPair(self.remote_player, self.local_player, track) for track in tracks]
		else:
			tracks = self.local_player.search_tracks(rating = True)
			self.logger.info('Attempting to match {} tracks'.format(len(tracks)))
			sync_pairs = [TrackPair(self.local_player, self.remote_player, track) for track in tracks]

		self.logger.info('Matching source tracks with destination player')
		matched = 0
		for pair in sync_pairs:
			if pair.match(): matched += 1
		self.logger.info('Matched {}/{} tracks'.format(matched, len(sync_pairs)))

		if self.options.dry: self.logger.info('Running a DRY RUN. No changes will be propagated!')
		pairs_need_update = [pair for pair in sync_pairs if pair.sync_state is SyncState.NEEDS_UPDATE]
		self.logger.info('Synchronizing {} matching tracks without conflicts'.format(len(pairs_need_update)))
		for pair in pairs_need_update:
			pair.sync()

		pairs_conflicting = [pair for pair in sync_pairs if pair.sync_state is SyncState.CONFLICTING]
		self.logger.info('{} pairs have conflicting ratings'.format(len(pairs_conflicting)))
		for pair in pairs_conflicting:
			pair.resolve_conflict()

	def sync_playlists(self):
		if self.options.reverse:
			raise NotImplementedError
		playlists = self.local_player.read_playlists()
		playlist_pairs = [PlaylistPair(self.local_player, self.remote_player, pl)
		                  for pl in playlists if not pl.is_auto_playlist]

		if self.options.dry: self.logger.info('Running a DRY RUN. No changes will be propagated!')

		self.logger.info('Matching local playlists with remote player')
		for pair in playlist_pairs:
			pair.match()

		self.logger.info('Synchronizing {} matching playlists'.format(len(playlist_pairs)))
		for pair in playlist_pairs:
			pair.sync()

def parse_args():
	parser = configargparse.ArgumentParser(default_config_files=['./config.ini'],description='Synchronizes ID3 music ratings with a Plex media-server')
	parser.add_argument('--dry', action='store_true', help='Does not apply any changes')
	parser.add_argument('--reverse', action='store_true', help='Syncs ratings from Plex to local player')
	parser.add_argument('--full', action='store_true', help='Force full synchronization')
	parser.add_argument('--sync', nargs='*', default=['tracks'], help='Selects which items to sync: one or more of [tracks, playlists]')
	parser.add_argument('--log', default='info', help='Sets the logging level')
	parser.add_argument('--passwd', type=str, help='The password for the plex user. NOT RECOMMENDED TO USE!')
	parser.add_argument('--player', default='MediaMonkey', type=str, help='Media player to synchronize with Plex')
	parser.add_argument('--server', type=str, required=True, help='The name of the plex media server')
	parser.add_argument('--username', type=str, required=True, help='The plex username')
	parser.add_argument('--token', type=str, help='Plex API token.  See https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/ for information on how to find your token')

	return parser.parse_args()

if __name__ == "__main__":
	locale.setlocale(locale.LC_ALL, '')
	args = parse_args()
	sync_agent = PlexSync(args)
	sync_agent.sync()
