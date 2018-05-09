# -*- coding: utf-8 -*-
# Copyright 2017, 2018 New Vector Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
from twisted.internet import defer, reactor

from ._base import BaseHandler
from synapse.types import UserID, create_requester
from synapse.util.logcontext import run_in_background

import logging

logger = logging.getLogger(__name__)


class DeactivateAccountHandler(BaseHandler):
    """Handler which deals with deactivating user accounts."""
    def __init__(self, hs):
        super(DeactivateAccountHandler, self).__init__(hs)
        self._auth_handler = hs.get_auth_handler()
        self._device_handler = hs.get_device_handler()
        self._room_member_handler = hs.get_room_member_handler()

        self._user_parter_running = False

        reactor.callWhenRunning(self.start_user_parting)

    @defer.inlineCallbacks
    def deactivate_account(self, user_id):
        """Deactivate a user's account

        Args:
            user_id (str): ID of user to be deactivated

        Returns:
            Deferred
        """
        # FIXME: Theoretically there is a race here wherein user resets
        # password using threepid.

        # first delete any devices belonging to the user, which will also
        # delete corresponding access tokens.
        yield self._device_handler.delete_all_devices_for_user(user_id)
        # then delete any remaining access tokens which weren't associated with
        # a device.
        yield self._auth_handler.delete_access_tokens_for_user(user_id)

        yield self.store.user_delete_threepids(user_id)
        yield self.store.user_set_password_hash(user_id, None)

        yield self.store.add_user_pending_deactivation(user_id)

        self.start_user_parting()

    def start_user_parting(self):
        if not self._user_parter_running:
            run_in_background(self.user_parter_loop())

    @defer.inlineCallbacks
    def user_parter_loop(self):
        self._user_parter_running = True
        logger.info("Starting user parter")
        try:
            while True:
                user_id = yield self.store.get_user_pending_deactivation()
                if user_id is None:
                    break
                logger.info("User parter parting %r", user_id)
                yield self.part_user(user_id)
                yield self.store.del_user_pending_deactivation(user_id)
                logger.info("User parter finished parting %r", user_id)
            logger.info("User parter finished: stopping")
        finally:
            self._user_parter_running = False

    @defer.inlineCallbacks
    def part_user(self, user_id):
        user = UserID.from_string(user_id)

        rooms_for_user = yield self.store.get_rooms_for_user(user_id)
        for room_id in rooms_for_user:
            logger.info("User parter parting %r from %r", user_id, room_id)
            yield self._room_member_handler.update_membership(
                create_requester(user),
                user,
                room_id,
                "leave",
                ratelimit=False,
            )
