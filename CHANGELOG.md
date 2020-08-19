# v1.0.7:
- =map command should now work in all situations
- Fixed match status message on faction selection
- Added possibility to freeze and unfreeze channels

# v1.0.6:
- Fixed critical bug in account distribution (every account could only be used 1 time)
- Match lenght is now 10 minutes
- ALL commands are now case insensitive (it was the case of only a few)
- Fixed a bug when lobby is stuck but a match spot becomes available
- "Round 2 is over" message is no longer displayed twice
- Staff can now properly cancel an ongoing match

# v1.0.5:
- Added =pog command for version checking and locking/unlocking the bot
- Now ignoring messages posted in wrong channels
- Added help regarding notify feature
- Added link to Jaeger Calendar

# v1.0.4:
- Fixed a bug when several matches are happening at the same time
- Customized discord.ext.tasks as lib.tasks to have better flexibility on tasks
- Various fixes

# v1.0.3:
- It is no longer possible to register with a character that is already registered
- Team captains can now select a map
- Added =confirm command for Team Captains to agree on a map
- Added role updates when agreeing with the rules
- Added notify feature
- Added =unregister @player to remove a player from the system (including db)

# v1.0.2:
- Now properly checking if player have no missing faction when registering with a Jaeger char
- Lobby size can now be modified from the config file
- Overhauled the mechanic removing afk players from lobby: Now players have to stay 15 minutes offline to be removed. If they come online within these 15 minutes, the timeout resets.
- Fixed incorrect match numeration
- Last player is now pinged when automatically assigned to a team
- Matches are now constituted of 2 rounds
- Round number is now displayed

# v1.0.1:
- Rules acceptance feature
- Registration of users with their Jaeger accounts, linked with ps2 api and a backend database
- One lobby, multi matches system
- Lobby size can be modified
- All parameters are in a config file easily editable
- Slight anti spam system (user must wait for their message to be processed before sending another one, especially with the long commands doing api calls for example)
- Several matches can happen at the same time
- Afk player are removed from lobby
- Captains selected at random temp
- Picking feature
- Faction picking feature
- Map is chosen at random within the PIL map pool temp
- Staff can change the map to any ps2 base (linked to ps2 api)
- Automatic handing of Jaeger accounts with precise tracking in a google sheet for good human visibility
- Ability for staff to clear a match anytime before it actually starts
- Each captain can toggle their "ready state"
- Match starts only when both teams are ready
