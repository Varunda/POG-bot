import modules.config as cfg
from modules.exceptions import UnexpectedError, AccountsNotEnough, ElementNotFound
from modules.display import channelSend, edit
from modules.enumerations import PlayerStatus, MatchStatus, SelStatus
from datetime import datetime as dt
from modules import ts3

from classes.teams import Team  # ok
from classes.players import TeamCaptain, ActivePlayer  # ok
from classes.maps import MapSelection  # ok
from classes.accounts import AccountHander  # ok

from random import choice as randomChoice
from lib import tasks
from asyncio import sleep

_lobbyList = list()
_lobbyStuck = False
_allMatches = dict()


def getMatch(id):
    if id not in _allMatches:
        raise ElementNotFound(id)  # should never happen
    return _allMatches[id]


def isLobbyStuck():
    global _lobbyStuck
    return _lobbyStuck


def addToLobby(player):
    _lobbyList.append(player)
    player.status = PlayerStatus.IS_LOBBIED
    if len(_lobbyList) == cfg.general["lobby_size"]:
        startMatchFromFullLobby.start()


def getLobbyLen():
    return len(_lobbyList)


def getAllNamesInLobby():
    names = [p.mention for p in _lobbyList]
    return names


def removeFromLobby(player):
    global _lobbyStuck
    _lobbyList.remove(player)
    _lobbyStuck = False
    player.status = PlayerStatus.IS_REGISTERED


def _onMatchFree():
    if len(_lobbyList) == cfg.general["lobby_size"]:
        startMatchFromFullLobby.start()


@tasks.loop(count=1)
async def startMatchFromFullLobby():
    global _lobbyStuck
    match = _findSpotForMatch()
    if match == None:
        _lobbyStuck = True
        await channelSend("LB_STUCK", cfg.discord_ids["lobby"])
        return
    match._setPlayerList(_lobbyList)
    for p in _lobbyList:
        p.match = match  # Player status is modified automatically in IS_MATCHED
    _lobbyList.clear()
    match._launch.start()
    # ts3: lobby full
    ts3.lobby_bot.move("281")
    if match.id == cfg.discord_ids["matches"][0]:
        ts3.lobby_bot.enqueue("663c62b8-5083-49d0-bed2-29ead13b749c")
        await channelSend("LB_MATCH_STARTING", cfg.discord_ids["lobby"], match.id)
        await sleep(8)
        ts3.lobby_bot.move("286")
    elif match.id == cfg.discord_ids["matches"][1]:
        ts3.lobby_bot.enqueue("540f93a5-9ccb-4cdc-8faf-4f3458625c80")
        await channelSend("LB_MATCH_STARTING", cfg.discord_ids["lobby"], match.id)
        await sleep(8)
        ts3.lobby_bot.move("323")


def onPlayerInactive(player):
    if player.status == PlayerStatus.IS_LOBBIED:
        player.onInactive.start(onInactiveConfirmed)


def onPlayerActive(player):
    if player.status == PlayerStatus.IS_LOBBIED:
        player.onInactive.cancel()


async def onInactiveConfirmed(player):
    removeFromLobby(player)
    await channelSend("LB_WENT_INACTIVE", cfg.discord_ids["lobby"], player.mention, namesInLobby=getAllNamesInLobby())


def clearLobby():
    if len(_lobbyList) == 0:
        return False
    for p in _lobbyList:
        p.status = PlayerStatus.IS_REGISTERED
    _lobbyList.clear()
    return True


def _findSpotForMatch():
    for match in _allMatches.values():
        if match.status == MatchStatus.IS_FREE:
            return match
    return None


def init(list):
    for id in list:
        Match(id)


class Match:
    def __init__(self, id):
        self.__id = id
        self.__players = dict()
        self.__status = MatchStatus.IS_FREE
        self.__teams = [None, None]
        self.__mapSelector = None
        self.__number = 0
        _allMatches[id] = self
        self.__accounts = None
        self.__roundsStamps = list()

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
    def statusString(self):
        return self.__status.value

    @property
    def number(self):
        return self.__number

    @number.setter
    def number(self, num):
        self.__number = num

    @property
    def playerPings(self):
        pings = [p.mention for p in self.__players.values()]
        return pings

    def _setPlayerList(self, pList):
        self.__status = MatchStatus.IS_RUNNING
        for p in pList:
            self.__players[p.id] = p

    def pick(self, team, player):
        team.addPlayer(ActivePlayer, player)
        self.__players.pop(player.id)
        team.captain.isTurn = False
        other = self.__teams[team.id - 1]
        other.captain.isTurn = True
        if len(self.__players) == 1:
            # Auto pick last player
            p = [*self.__players.values()][0]
            self.__pingLastPlayer.start(other, p)
            return self.pick(other, p)
        if len(self.__players) == 0:
            self.__status = MatchStatus.IS_FACTION
            self.__teams[1].captain.isTurn = True
            self.__teams[0].captain.isTurn = False
            picker = self.__teams[1].captain
            self.__playerPickOver.start(picker)
            return self.__teams[1].captain
        return other.captain

    def confirmMap(self):
        self.__mapSelector.confirm()
        self.__ready.start()

    def pickMap(self, captain):
        if self.__mapSelector.status == SelStatus.IS_SELECTED:
            captain.isTurn = False
            other = self.__teams[captain.team.id - 1]
            other.captain.isTurn = True
            return other.captain
        return captain

    @tasks.loop(count=1)
    async def __pingLastPlayer(self, team, p):
        await channelSend("PK_LAST", self.__id, p.mention, team.name)

    @tasks.loop(count=1)
    async def __playerPickOver(self, picker):
        await channelSend("PK_OK_FACTION", self.__id, picker.mention, match=self)
        # ts3: select faction
        ts3.lobby_bot.enqueue("91da596c-2494-4dbc-9c59-71f74f3b68cb")

    def factionPick(self, team, str):
        faction = cfg.i_factions[str]
        other = self.__teams[team.id - 1]
        if other.faction == faction:
            return team.captain
        team.faction = faction
        team.captain.isTurn = False
        if other.faction != 0:
            self.__status = MatchStatus.IS_MAPPING
            self.__findMap.start()
        else:
            other.captain.isTurn = True
        return other.captain

    def onTeamReady(self, team):
        # ts3: ready up
        notReady = list()
        for p in team.players:
            if p.hasOwnAccount:
                continue
            if p.account == None:
                print(f"Debug: {p.name} has no account")
            if p.account.isValidated:
                continue
            notReady.append(p.mention)
        if len(notReady) != 0:
            return notReady
        team.captain.isTurn = False
        other = self.__teams[team.id - 1]
        # If other isTurn, then not ready
        # Else everyone ready
        if not other.captain.isTurn:
            # ts3: 30s
            self.__status = MatchStatus.IS_STARTING
            self.__startMatch.start()

    @tasks.loop(count=1)
    async def __findMap(self):
        # LEGACY CODE
        # Disabling map at random:
        # if self.__map == None:
        #     try:
        #         sel = getMapSelection(self.__id)
        #     except ElementNotFound:
        #         sel = MapSelection(self.__id)
        #     sel.selectFromIdList(cfg.PIL_MAPS_IDS)
        #     if sel.status not in (SelStatus.IS_SELECTED, SelStatus.IS_SELECTION):
        #         await channelSend("UNKNOWN_ERROR", self.__id, "Can't find a map at random!")
        #         return
        #     self.__map = randomChoice(sel.selection)
        for tm in self.__teams:
            tm.captain.isTurn = True
        if self.__mapSelector.status == SelStatus.IS_CONFIRMED:
            await channelSend("MATCH_MAP_AUTO", self.__id, self.__map.name)
            # ts3: map selected
            ts3.lobby_bot.enqueue("ecf7b363-68dc-45d4-a69d-25553ce1f776")
            self.__ready.start()
            return
        captainPings = [tm.captain.mention for tm in self.__teams]
        self.__status = MatchStatus.IS_MAPPING
        # ts3: select map
        await sleep(1)  # prevents bug when enqueuing songs too quickly
        ts3.lobby_bot.enqueue("21bb87b5-5279-45fd-90a7-7c31b5e5199d")
        await channelSend("PK_WAIT_MAP", self.__id, *captainPings)

    @tasks.loop(count=1)
    async def __ready(self):
        for tm in self.__teams:
            tm.matchReady()
            tm.captain.isTurn = True
        captainPings = [tm.captain.mention for tm in self.__teams]
        try:
            await self.__accounts.give_accounts()
        except AccountsNotEnough:
            await channelSend("ACC_NOT_ENOUGH", self.__id)
            await self.clear()
            return
        self.__status = MatchStatus.IS_WAITING
        await channelSend("MATCH_CONFIRM", self.__id, *captainPings, match=self)
        # ts3: type =ready
        await sleep(10)
        ts3.lobby_bot.enqueue("23ee2e94-8628-4285-aeee-d07e95a2f2ee")
        ts3.team2_bot.enqueue("23ee2e94-8628-4285-aeee-d07e95a2f2ee")

    @tasks.loop(minutes=cfg.ROUND_LENGHT, delay=1, count=2)
    async def __onMatchOver(self):
        playerPings = [tm.allPings for tm in self.__teams]
        await channelSend("MATCH_ROUND_OVER", self.__id, *playerPings, self.roundNo)
        # ts3: round over
        for tm in self.__teams:
            tm.captain.isTurn = True
        if self.roundNo < 2:
            await channelSend("MATCH_SWAP", self.__id)
            # ts3: swap sundies
            await sleep(1)
            ts3.lobby_bot.enqueue("1ae444aa-12db-40da-b341-9ff98962829e")
            ts3.team2_bot.enqueue("1ae444aa-12db-40da-b341-9ff98962829e")
            await sleep(1)
            self.__status = MatchStatus.IS_WAITING
            captainPings = [tm.captain.mention for tm in self.__teams]
            await channelSend("MATCH_CONFIRM", self.__id, *captainPings, match=self)
            ts3.lobby_bot.enqueue("23ee2e94-8628-4285-aeee-d07e95a2f2ee")
            ts3.team2_bot.enqueue("23ee2e94-8628-4285-aeee-d07e95a2f2ee")
            return
        await channelSend("MATCH_OVER", self.__id)
        self.__status = MatchStatus.IS_RUNNING
        await self.clear()

    @tasks.loop(count=1)
    async def __startMatch(self):
        await channelSend("MATCH_STARTING_1", self.__id, self.roundNo, "30")
        # ts3: 30s
        ts3.lobby_bot.play("ff4d8310-06cd-449f-8d8e-00dda43dc102")
        ts3.team2_bot.play("ff4d8310-06cd-449f-8d8e-00dda43dc102")
        await sleep(10)
        await channelSend("MATCH_STARTING_2", self.__id, self.roundNo, "20")
        # ts3: 10s
        await sleep(8)
        ts3.lobby_bot.play("2b68e6d3-6009-4662-a72e-9d9e2b84d67b")
        ts3.team2_bot.play("2b68e6d3-6009-4662-a72e-9d9e2b84d67b")
        await sleep(2)
        await channelSend("MATCH_STARTING_2", self.__id, self.roundNo, "10")
        await sleep(3.2)
        # ts3: 5s
        ts3.lobby_bot.play("83693496-a410-4602-80dd-9ed74ec0a2fe")
        ts3.team2_bot.play("83693496-a410-4602-80dd-9ed74ec0a2fe")
        await sleep(6.8)
        playerPings = [tm.allPings for tm in self.__teams]
        await channelSend("MATCH_STARTED", self.__id, *playerPings, self.roundNo)
        self.__roundsStamps.append(int(dt.timestamp(dt.now())))
        self.__status = MatchStatus.IS_PLAYING
        self.__onMatchOver.start()

    @tasks.loop(count=1)
    async def _launch(self):
        await channelSend("MATCH_INIT", self.__id, " ".join(self.playerPings))
        self.__accounts = AccountHander(self)
        self.__mapSelector = MapSelection(self)
        for i in range(len(self.__teams)):
            self.__teams[i] = Team(i, f"Team {i + 1}", self)
            key = randomChoice(list(self.__players))
            self.__teams[i].addPlayer(TeamCaptain, self.__players.pop(key))
        self.__teams[0].captain.isTurn = True
        self.__status = MatchStatus.IS_PICKING
        await channelSend("MATCH_SHOW_PICKS", self.__id, self.__teams[0].captain.mention, match=self)
        # ts3: select teams
        await sleep(10)
        ts3.lobby_bot.enqueue("6eb6f5cc-99bd-4df6-9b59-bc85bd88ba7c")

    async def clear(self):
        """ Clearing match and base player objetcts
        Team and ActivePlayer objects should get garbage collected, nothing is referencing them anymore"""

        if self.status == MatchStatus.IS_PLAYING:
            self.__onMatchOver.cancel()
            playerPings = [tm.allPings for tm in self.__teams]
            await channelSend("MATCH_ROUND_OVER", self.__id, *playerPings, self.roundNo)
            # ts3: round over
            ts3.lobby_bot.play("a0fbc373-13e7-4f14-81ec-47b40be7ab13")
            ts3.team2_bot.play("a0fbc373-13e7-4f14-81ec-47b40be7ab13")
            await channelSend("MATCH_OVER", self.__id)
            # ts3: round over

        # Updating account sheet with current match
        await self.__accounts.doUpdate()

        # Clean players if left in the list
        for p in self.__players.values():
            p.clean()

        # Clean players if in teams
        for tm in self.__teams:
            for p in tm.players:
                p.clean()

        # Clean mapSelector
        self.__mapSelector.clean()

        # Release all objects:
        self.__accounts = None
        self.__mapSelector = None
        self.__teams = [None, None]
        self.__roundsStamps.clear()
        self.__players.clear()
        await channelSend("MATCH_CLEARED", self.__id)
        self.__status = MatchStatus.IS_FREE
        _onMatchFree()

    @property
    def map(self):
        if self.__mapSelector.status == SelStatus.IS_CONFIRMED:
            return self.__mapSelector.map

    # TODO: testing only
    @property
    def players(self):
        return self.__players

    @property
    def roundNo(self):
        if self.__status == MatchStatus.IS_PLAYING:
            return len(self.__roundsStamps)
        if self.__status in (MatchStatus.IS_STARTING, MatchStatus.IS_WAITING):
            return len(self.__roundsStamps) + 1
        return 0

    @property
    def mapSelector(self):
        return self.__mapSelector
