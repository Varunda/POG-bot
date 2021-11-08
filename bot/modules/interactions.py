from modules.spam_checker import is_spam, unlock
from discord import Interaction
from logging import getLogger
from inspect import iscoroutinefunction as is_coroutine
from discord.errors import NotFound
from lib.tasks import Loop

log = getLogger("pog_bot")


class InteractionNotAllowed(Exception):
    pass


class InteractionInvalid(Exception):
    def __init__(self, msg):
        self.reason = msg
        message = "Invalid interaction: " + msg
        log.error(message)
        super().__init__(message)


class InteractionHandler:
    def __init__(self, disable_after_use=True, single_callback=None):
        self.__disable_after_use = disable_after_use
        self.__f_dict = dict()
        self.__callback = single_callback
        self.__msg = None
        self.__locked = False

    async def show(self, disp_object, ctx, *args, **kwargs):
        self.__locked = True
        if self.__msg:
            self.clean()
        kwargs['callback'] = self.run
        self.__msg = await disp_object.send(ctx, *args, **kwargs)
        self.__locked = False

    async def run(self, interaction: Interaction):
        if self.__locked:
            return

        user = interaction.user

        if await is_spam(user, interaction.message.channel):
            return

        self.__locked = True

        interaction_id = interaction.data['custom_id']
        interaction_values = interaction.data.get('values', None)

        try:
            if not self.__callback:
                funcs = self.__f_dict[interaction_id]
            else:
                funcs = [self.__callback]
            for func in funcs:
                if is_coroutine(func):
                    await func(interaction_id, interaction, interaction_values)
                else:
                    func(interaction_id, interaction, interaction_values)
                if self.__disable_after_use:
                    self.clean()
        except (KeyError, InteractionNotAllowed, NotFound, InteractionInvalid):
            pass
        finally:
            self.__locked = False
            unlock(user.id)

    def add_callback(self, custom_id, fct):
        if custom_id not in self.__f_dict:
            self.__f_dict[custom_id] = list()
        self.__f_dict[custom_id].append(fct)

    def callback(self, *args):
        def decorator(func):
            for custom_id in args:
                self.add_callback(custom_id, func)
            return func
        return decorator

    def clean(self):
        self.__locked = True
        if self.__msg:
            Loop(coro=self._remove_msg, count=1).start(self.__msg)
        self.__msg = None

    async def _remove_msg(self, msg):
        try:
            await msg.edit(view=None)
        except NotFound:
            pass
