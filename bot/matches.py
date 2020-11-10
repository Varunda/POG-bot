import modules.config as cfg
from modules.exceptions import UnexpectedError, AccountsNotEnough, \
    ElementNotFound
from display import channel_send
from modules.enumerations import PlayerStatus, MatchStatus, SelStatus
from modules.image_maker import publish_match_image
from modules.census import process_score, get_offline_players
from modules.database import update_match
from datetime import datetime as dt, timezone as tz
from modules.ts3 import REGEX_getTs3Bots
from modules.ts_interface import faction_audio, map_audio, which_bot, which_pick_channels, which_team_channels

from classes.teams import Team  # ok
from classes.players import TeamCaptain, ActivePlayer  # ok
from classes.maps import MapSelection, mainmap_pool  # ok
from classes.accounts import AccountHander  # ok

from random import choice as random_choice
from lib.tasks import loop
from asyncio import sleep
from logging import getLogger

log = getLogger(__name__)

_lobby_list = list()
_lobbyStuck = False
_allMatches = dict()


def get_match(id):
    if id not in _allMatches:
        raise ElementNotFound(id)  # should never happen
    return _allMatches[id]


def is_lobby_stuck():
    global _lobbyStuck
    return _lobbyStuck


def _autoPingThreshold():
    thresh = cfg.general["lobby_size"] - cfg.general["lobby_size"] // 3
    return thresh


def _autoPingCancel():
    _autoPing.cancel()
    _autoPing.already = False

def _getSub():
    if len(_lobby_list) == 0:
        return
    player = random_choice(_lobby_list)
    _lobby_list.remove(player)
    _onLobbyRemove()
    return player


def add_to_lobby(player):
    _lobby_list.append(player)
    player.on_lobby_add()
    if len(_lobby_list) == cfg.general["lobby_size"]:
        start_match_from_full_lobby.start()
    elif len(_lobby_list) >= _autoPingThreshold():
        if not _autoPing.is_running() and not _autoPing.already:
            _autoPing.start()
            _autoPing.already = True


@loop(minutes=3, delay=1, count=2)
async def _autoPing():
    if _findSpotForMatch() is None:
        return
    await channel_send("LB_NOTIFY", cfg.channels["lobby"], f'<@&{cfg.roles["notify"]}>')
_autoPing.already = False


def get_lobby_len():
    return len(_lobby_list)


def get_all_names_in_lobby():
    names = [p.mention for p in _lobby_list]
    return names


def get_all_ids_in_lobby():
    ids = [str(p.id) for p in _lobby_list]
    return ids


def remove_from_lobby(player):
    _lobby_list.remove(player)
    _onLobbyRemove()
    player.on_lobby_leave()


def _onMatchFree():
    _autoPing.already = True
    if len(_lobby_list) == cfg.general["lobby_size"]:
        start_match_from_full_lobby.start()


def _onLobbyRemove():
    global _lobbyStuck
    _lobbyStuck = False
    if len(_lobby_list) < _autoPingThreshold():
        _autoPingCancel()


@loop(count=1)
async def start_match_from_full_lobby():
    global _lobbyStuck
    match = _findSpotForMatch()
    _autoPingCancel()
    if match is None:
        _lobbyStuck = True
        await channel_send("LB_STUCK", cfg.channels["lobby"])
        return
    _lobbyStuck = False
    match._setPlayerList(_lobby_list)
    for p in _lobby_list:
        p.on_match_selected(match)
    _lobby_list.clear()
    match._launch.start()
    await channel_send("LB_MATCH_STARTING", cfg.channels["lobby"], match.id)
    # ts3: lobby full
    if match.id == cfg.channels["matches"][0]:  # if match 1
        REGEX_getTs3Bots()[0].move(cfg.teamspeak_ids["ts_lobby"])  # IF IT HANGS HERE MAKE SURE webapi.js IS ENABLED FOR SINUSBOT
        REGEX_getTs3Bots()[0].enqueue(cfg.audio_ids["drop_match_1_picks"])
        await sleep(REGEX_getTs3Bots()[0].get_duration(cfg.audio_ids["drop_match_1_picks"]))
        REGEX_getTs3Bots()[0].move(cfg.teamspeak_ids["ts_match_1_picks"])
    elif match.id == cfg.channels["matches"][1]:  # if match 2
        REGEX_getTs3Bots()[1].move(cfg.teamspeak_ids["ts_lobby"])
        REGEX_getTs3Bots()[1].enqueue(cfg.audio_ids["drop_match_2_picks"])
        await sleep(REGEX_getTs3Bots()[1].get_duration(cfg.audio_ids["drop_match_2_picks"]))
        REGEX_getTs3Bots()[1].move(cfg.teamspeak_ids["ts_match_2_picks"])
    elif match.id == cfg.channels["matches"][2]:  # if match 3
        REGEX_getTs3Bots()[1].move(cfg.teamspeak_ids["ts_lobby"])
        REGEX_getTs3Bots()[1].enqueue(cfg.audio_ids["drop_match_3_picks"])
        await sleep(REGEX_getTs3Bots()[1].get_duration(cfg.audio_ids["drop_match_3_picks"]))
        REGEX_getTs3Bots()[1].move(cfg.teamspeak_ids["ts_match_3_picks"])


async def on_inactive_confirmed(player):
    remove_from_lobby(player)
    await channel_send("LB_WENT_INACTIVE", cfg.channels["lobby"], player.mention, names_in_lobby=get_all_names_in_lobby())


def clear_lobby():
    if len(_lobby_list) == 0:
        return False
    for p in _lobby_list:
        p.on_lobby_leave()
    _lobby_list.clear()
    _onLobbyRemove()
    return True


def _findSpotForMatch():
    for match in _allMatches.values():
        if match.status is MatchStatus.IS_FREE:
            return match
    return None


def init(client, list):
    for m_id in list:
        ch = client.get_channel(m_id)
        Match(m_id, ch)


class Match():

    def __init__(self, m_id, ch=None):
        self.__id = m_id
        self.__channel = ch
        self.__players = dict()
        self.__status = MatchStatus.IS_FREE
        self.__teams = [None, None]
        self.__mapSelector = None
        self.__number = 0
        self.__resultMsg = None
        _allMatches[m_id] = self
        self.__accounts = None
        self.__roundStamps = list()

    @classmethod
    def new_from_data(cls, data):
        obj = cls(data["_id"])
        obj.__roundStamps = data["round_stamps"]
        obj.__mapSelector = MapSelection.new_from_id(data["_id"], data["base_id"])
        for i in range(len(data["teams"])):
            obj.__teams[i] = Team.new_from_data(i, data["teams"][i], obj)
        return obj

    @property
    def channel(self):
        return self.__channel

    @property
    def msg(self):
        return self.__resultMsg

    @property
    def status(self):
        return self.__status

    @property
    def id(self):
        return self.__id

    @property
    def teams(self):
        return self.__teams

    @property
    def status_string(self):
        return self.__status.value

    @property
    def number(self):
        return self.__number

    @number.setter
    def number(self, num):
        self.__number = num

    @property
    def player_pings(self):
        pings = [p.mention for p in self.__players.values()]
        return pings

    @property
    def seconds_to_round_end(self):
        time_delta = self._onMatchOver.next_iteration - dt.now(tz.utc)
        return int(time_delta.total_seconds())

    @property
    def formated_time_to_round_end(self):
        secs = self.seconds_to_round_end
        return f"{secs//60}m {secs%60}s"

    def get_data(self):
        teams_data = list()
        for tm in self.__teams:
            teams_data.append(tm.get_data())
        data = {"_id": self.__number,
                "round_stamps": self.__roundStamps,
                "round_length_min": cfg.ROUND_LENGTH,
                "base_id": self.__mapSelector.map.id,
                "teams": teams_data
                }
        return data


    def _setPlayerList(self, p_list):
        self.__status = MatchStatus.IS_RUNNING
        for p in p_list:
            self.__players[p.id] = p

    def pick(self, team, player):
        team.add_player(ActivePlayer, player)
        self.__players.pop(player.id)
        team.captain.is_turn = False
        other = self.__teams[team.id - 1]
        other.captain.is_turn = True
        if len(self.__players) == 1:
            # Auto pick last player
            p = [*self.__players.values()][0]
            self.__pingLastPlayer.start(other, p)
            return self.pick(other, p)
        if len(self.__players) == 0:
            self.__status = MatchStatus.IS_FACTION
            self.__teams[1].captain.is_turn = True
            self.__teams[0].captain.is_turn = False
            picker = self.__teams[1].captain
            self.__playerPickOver.start(picker)
            return self.__teams[1].captain
        return other.captain

    def confirm_map(self):
        self.__mapSelector.confirm()
        if self.__status is MatchStatus.IS_MAPPING:
            self.__ready.start()

    def pick_map(self, captain):
        if self.__mapSelector.status is SelStatus.IS_SELECTED:
            captain.is_turn = False
            other = self.__teams[captain.team.id - 1]
            other.captain.is_turn = True
            return other.captain
        return captain

    def resign(self, captain):
        team = captain.team
        if team.is_players:
            return False
        else:
            player = captain.on_resign()
            key = random_choice(list(self.__players))
            self.__players[player.id] = player
            team.clear()
            team.add_player(TeamCaptain, self.__players.pop(key))
            team.captain.is_turn = captain.is_turn
            return True

    @loop(count=1)
    async def __pingLastPlayer(self, team, p):
        await channel_send("PK_LAST", self.__id, p.mention, team.name)

    @loop(count=1)
    async def __playerPickOver(self, picker):
        await channel_send("PK_OK_FACTION", self.__id, picker.mention, match=self)
        # ts3: select faction
        ts3bot = which_bot(self.__id)
        pick_channel = which_pick_channels(self.__id)
        ts3bot.move(pick_channel)
        ts3bot.enqueue(cfg.audio_ids["select_factions"])

    def faction_pick(self, team, arg):
        faction = cfg.i_factions[arg.upper()]
        other = self.__teams[team.id-1]
        if other.faction == faction:
            return team.captain
        team.faction = faction
        team.captain.is_turn = False
        faction_audio(team)
        if other.faction != 0:
            self.__status = MatchStatus.IS_MAPPING
            self.__findMap.start()
        else:
            other.captain.is_turn = True
        return other.captain

    def faction_change(self, team, arg):
        faction = cfg.i_factions[arg.upper()]
        other = self.__teams[team.id-1]
        if other.faction == faction:
            return False
        team.faction = faction
        return True

    def on_player_sub(self, subbed):
        new_player = _getSub()
        if new_player is None:
            return
        new_player.on_match_selected(self)
        if subbed.status is PlayerStatus.IS_MATCHED:
            del self.__players[subbed.id]
            self.__players[new_player.id] = new_player
        elif subbed.status is PlayerStatus.IS_PICKED:
            a_sub = subbed.active
            a_sub.team.on_player_sub(a_sub, new_player)
        subbed.on_player_clean()
        return new_player



    def on_team_ready(self, team):
        team.captain.is_turn = False
        team.on_team_ready()
        other = self.__teams[team.id-1]
        # If other is_turn, then not ready
        # Else everyone ready
        if not other.captain.is_turn:
            self.__status = MatchStatus.IS_STARTING
            self.__startMatch.start()

    @loop(count=1)
    async def __findMap(self):
        ts3bot = which_bot(self.__id)
        for tm in self.__teams:
            tm.captain.is_turn = True
        if self.__mapSelector.status is SelStatus.IS_CONFIRMED:
            await channel_send("MATCH_MAP_AUTO", self.__id, self.__mapSelector.map.name)
            # ts3: map selected
            pick_channel = which_pick_channels(self.__id)
            ts3bot.move(pick_channel)
            ts3bot.enqueue(cfg.audio_ids["map_selected"])
            self.__ready.start()
            return
        captain_pings = [tm.captain.mention for tm in self.__teams]
        self.__status = MatchStatus.IS_MAPPING
        # ts3: select map
        pick_channel = which_pick_channels(self.__id)
        ts3bot.move(pick_channel)
        await sleep(1)  # prevents playing this before faction announce
        ts3bot.enqueue(cfg.audio_ids["select_map"])
        msg = await channel_send("PK_WAIT_MAP", self.__id, *captain_pings, sel=self.__mapSelector)
        await self.__mapSelector.navigator.set_msg(msg)

    @loop(count=1)
    async def __ready(self):
        self.__status = MatchStatus.IS_RUNNING
        for tm in self.__teams:
            tm.on_match_ready()
            tm.captain.is_turn = True
        captain_pings = [tm.captain.mention for tm in self.__teams]
        await map_audio(self)
        try:
            await self.__accounts.give_accounts()
        except AccountsNotEnough:
            await channel_send("ACC_NOT_ENOUGH", self.__id)
            await self.clear()
            return
        except Exception as e:
            log.error(f"Error in account giving function!\n{e}")
            await channel_send("ACC_ERROR", self.__id)
            await self.clear()
            return

        self.__status = MatchStatus.IS_WAITING
        await channel_send("MATCH_CONFIRM", self.__id, *captain_pings, match=self)
        # ts3: type =ready
        await sleep(10)  # waits long enough for people to move to their team's channels
        team_channels = which_team_channels(self.__id)
        REGEX_getTs3Bots()[0].move(team_channels[0])
        REGEX_getTs3Bots()[1].move(team_channels[1])
        REGEX_getTs3Bots()[0].enqueue(cfg.audio_ids["type_ready"])
        REGEX_getTs3Bots()[1].enqueue(cfg.audio_ids["type_ready"])

    @loop(minutes=cfg.ROUND_LENGTH, delay=1, count=2)
    async def _onMatchOver(self):
        player_pings = [" ".join(tm.all_pings) for tm in self.__teams]
        await channel_send("MATCH_ROUND_OVER", self.__id, *player_pings, self.round_no)
        self._scoreCalculation.start()
        # ts3: round over
        team_channels = which_team_channels(self.__id)
        REGEX_getTs3Bots()[0].move(team_channels[0])
        REGEX_getTs3Bots()[1].move(team_channels[1])
        REGEX_getTs3Bots()[0].play(cfg.audio_ids["round_over"])
        REGEX_getTs3Bots()[1].play(cfg.audio_ids["round_over"])
        for tm in self.__teams:
            tm.captain.is_turn = True
        if self.round_no < 2:
            await channel_send("MATCH_SWAP", self.__id)
            # ts3: swap sundies
            REGEX_getTs3Bots()[0].move(team_channels[0])
            REGEX_getTs3Bots()[1].move(team_channels[1])
            await sleep(0.1)  # prevents bug when enqueuing songs too quickly
            REGEX_getTs3Bots()[0].enqueue(cfg.audio_ids["switch_sides"])
            REGEX_getTs3Bots()[1].enqueue(cfg.audio_ids["switch_sides"])
            self.__status = MatchStatus.IS_WAITING
            captain_pings = [tm.captain.mention for tm in self.__teams]
            await channel_send("MATCH_CONFIRM", self.__id, *captain_pings, match=self)
            REGEX_getTs3Bots()[0].move(team_channels[0])
            REGEX_getTs3Bots()[1].move(team_channels[1])
            REGEX_getTs3Bots()[0].enqueue(cfg.audio_ids["type_ready"])
            REGEX_getTs3Bots()[1].enqueue(cfg.audio_ids["type_ready"])
            self._scoreCalculation.start()
            return
        await channel_send("MATCH_OVER", self.__id)
        self.__status = MatchStatus.IS_RESULT
        try:
            await update_match(self)
        except Exception as e:
            log.error(f"Error in match database push!\n{e}")
        try:
            await process_score(self)
            self.__resultMsg = await publish_match_image(self)
        except Exception as e:
            log.error(f"Error in score or publish function!\n{e}")
        await self.clear()

    @loop(count=1)
    async def _scoreCalculation(self):
        await process_score(self)
        self.__resultMsg = await publish_match_image(self)


    @loop(count=1)
    async def __startMatch(self):
        # ts3: ensure bots are in match team channels -- ideally add a check to ensure no matches start within 30s of each other
        team_channels = which_team_channels(self.__id)
        REGEX_getTs3Bots()[0].move(team_channels[0])
        REGEX_getTs3Bots()[1].move(team_channels[1])
        await channel_send("MATCH_STARTING_1", self.__id, self.round_no, "30")
        # ts3: 30s
        REGEX_getTs3Bots()[0].move(team_channels[0])
        REGEX_getTs3Bots()[1].move(team_channels[1])
        REGEX_getTs3Bots()[0].play(cfg.audio_ids["30s"])
        REGEX_getTs3Bots()[1].play(cfg.audio_ids["30s"])
        await sleep(10)
        await channel_send("MATCH_STARTING_2", self.__id, self.round_no, "20")
        # ts3: 10s
        await sleep(8)
        REGEX_getTs3Bots()[0].move(team_channels[0])
        REGEX_getTs3Bots()[1].move(team_channels[1])
        REGEX_getTs3Bots()[0].play(cfg.audio_ids["10s"])
        REGEX_getTs3Bots()[1].play(cfg.audio_ids["10s"])
        await sleep(2)
        await channel_send("MATCH_STARTING_2", self.__id, self.round_no, "10")
        await sleep(3.2)  # odd timings make sure the voice line plays at the right time
        # ts3: 5s
        REGEX_getTs3Bots()[0].move(team_channels[0])
        REGEX_getTs3Bots()[1].move(team_channels[1])
        REGEX_getTs3Bots()[0].play(cfg.audio_ids["5s"])
        REGEX_getTs3Bots()[1].play(cfg.audio_ids["5s"])
        await sleep(6.8)
        player_pings = [" ".join(tm.all_pings) for tm in self.__teams]
        await channel_send("MATCH_STARTED", self.__id, *player_pings, self.round_no)
        self.__roundStamps.append(int(dt.timestamp(dt.now())))
        self.__status = MatchStatus.IS_PLAYING
        self._onMatchOver.start()

    @loop(count=1)
    async def _launch(self):
        await channel_send("MATCH_INIT", self.__id, " ".join(self.player_pings))
        self.__accounts = AccountHander(self)
        self.__mapSelector = MapSelection(self, mainmap_pool)
        for i in range(len(self.__teams)):
            self.__teams[i] = Team(i, f"Team {i + 1}", self)
            key = random_choice(list(self.__players))
            self.__teams[i].add_player(TeamCaptain, self.__players.pop(key))
        self.__teams[0].captain.is_turn = True
        self.__status = MatchStatus.IS_PICKING
        await channel_send("MATCH_SHOW_PICKS", self.__id, self.__teams[0].captain.mention, match=self)
        # ts3: select teams
        await sleep(17)  # wait some time after the bot moves before announcing to select players for teams
        ts3bot = which_bot(self.__id)
        pick_channel = which_pick_channels(self.__id)
        ts3bot.move(pick_channel)
        ts3bot.enqueue(cfg.audio_ids["select_teams"])

    async def clear(self):
        """ Clearing match and base player objetcts
        Team and ActivePlayer objects should get garbage collected, nothing is referencing them anymore"""

        if self.status is MatchStatus.IS_PLAYING:
            self._onMatchOver.cancel()
            player_pings = [" ".join(tm.all_pings) for tm in self.__teams]
            await channel_send("MATCH_ROUND_OVER", self.__id, *player_pings, self.round_no)
            # ts3: round over
            team_channels = which_team_channels(self.__id)
            REGEX_getTs3Bots()[0].move(team_channels[0])
            REGEX_getTs3Bots()[1].move(team_channels[1])
            REGEX_getTs3Bots()[0].play(cfg.audio_ids["round_over"])
            REGEX_getTs3Bots()[1].play(cfg.audio_ids["round_over"])
            await channel_send("MATCH_OVER", self.__id)
            # ts3: round over

        # Updating account sheet with current match
        await self.__accounts.do_update()

        # Clean players if left in the list
        for p in self.__players.values():
            p.on_player_clean()

        # Clean players if in teams
        for tm in self.__teams:
            for a_player in tm.players:
                a_player.clean()

        # Clean map_selector
        self.__mapSelector.clean()

        # Release all objects:
        self.__accounts = None
        self.__mapSelector = None
        self.__teams = [None, None]
        self.__roundStamps.clear()
        self.__resultMsg = None
        self.__players.clear()
        await channel_send("MATCH_CLEARED", self.__id)
        self.__status = MatchStatus.IS_FREE
        _onMatchFree()
        await sleep(REGEX_getTs3Bots()[0].get_duration(cfg.audio_ids["round_over"]))
        REGEX_getTs3Bots()[0].move(cfg.teamspeak_ids["ts_lobby"])
        REGEX_getTs3Bots()[1].move(cfg.teamspeak_ids["ts_lobby"])

    @property
    def map(self):
        if self.__mapSelector.status is SelStatus.IS_CONFIRMED:
            return self.__mapSelector.map

    # TODO: testing only
    @property
    def players(self):
        return self.__players

    @property
    def round_no(self):
        if self.__status is MatchStatus.IS_PLAYING:
            return len(self.__roundStamps)
        if self.__status in (MatchStatus.IS_STARTING, MatchStatus.IS_WAITING):
            return len(self.__roundStamps) + 1
        return 0

    @property
    def start_stamp(self):
        return self.__roundStamps[-1]

    @property
    def round_stamps(self):
        return self.__roundStamps

    @property
    def map_selector(self):
        return self.__mapSelector

    # # DEV
    # @teams.setter
    # def teams(self, tms):
    #     self.__teams=tms
    
    # # DEV
    # @start_stamp.setter
    # def start_stamp(self, st):
    #     self.__roundStamps = st
    
    # # DEV
    # @map_selector.setter
    # def map_selector(self, ms):
    #     self.__mapSelector = ms

    # # DEV
    # @msg.setter
    # def msg(self, msg):
    #     self.__resultMsg = msg
    
    # #DEV
    # @status.setter
    # def status(self, bl):
    #     if bl:
    #         self.__status = MatchStatus.IS_RESULT
    #     else:
    #         self.__status = MatchStatus.IS_PLAYING