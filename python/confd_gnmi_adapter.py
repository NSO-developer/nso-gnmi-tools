from __future__ import annotations
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from queue import Queue, Empty
from typing import List

import gnmi_pb2

log = logging.getLogger('confd_gnmi_adapter')


class GnmiServerAdapter(ABC):
    @dataclass
    class CapabilityModel:
        name: str
        organization: str
        version: str
        schema: str = ""

    @abstractmethod
    def capabilities(self):
        """
        Invoke capabilities on adapter and return list of Capabilities
        :return: list of  CapabilityModel elements (schema is empty string for now TODO)
        """
        pass

    @abstractmethod
    def encodings(self) -> list[int]:
        """
        Return list of encodings supported by server.
        """
        pass

    @abstractmethod
    def get(self, prefix, paths, data_type, use_models):
        """
        Invoke get operation on adapter and return list of notifications
        :param prefix:  gnmi_pb2.Path with prefix for all paths
        :param paths: list of gnmi_pb2.Path elements to get data subtrees
        :param data_type: gnmi_pb2.DataType (ALL, CONFIG, STATE, OPERATIONAL)
        :param use_models: list of gnmi_pb2.ModelData  elements to use
        :return: list of gnmi_pb2.Notification were
                  prefix - is prefix
                  updated - list of gnmi_pb2.Update elements (one for each path)
                  delete - empty list
                  timestamp - current time in nanoseconds since Epoch
                  atomic - not set (TODO)
        Note: iteration of all paths is in adapter, so adapter can make optimization,
            if possible
        """
        pass

    @abstractmethod
    def set(self, prefix, updates):
        """
        Apply given updates.
        :param prefix: gNMI path prefix
        :param updates: gNMI updates (with path and val) to be set
        :return: gNMI UpdateResult operation
        """
        pass

    @abstractmethod
    def delete(self, prefix, paths):
        """
        Delete value(s) for given path
        TODO this is simple version for initial implementation
        To reflect fully gNMI Set,
        we should pass all delete, replace and update lists
        :param prefix: gNMI path prefix
        :param paths: list of gNMI paths to delete
        :return: gNMI UpdateResult operation
        """
        pass

    class SubscriptionHandler(ABC):

        class SubscriptionEvent(Enum):
            SAMPLE = 0
            SEND_CHANGES = 1
            FINISH = 10
            ASYNC_FINISH = 15

        def __init__(self, adapter, subscription_list):
            self.adapter: GnmiServerAdapter = adapter
            self.subscription_list = subscription_list
            self.read_queue = None
            self.monitoring = False
            if not self.is_once():
                self.read_queue = Queue()
                self.monitoring = self._is_monitor_changes()

        @abstractmethod
        def get_sample(self, path, prefix, allow_aggregation=False) -> List:
            """
            Create gNMI subscription updates for given path and prefix
            :param path: gNMI path for updates
            :param prefix: gNMI prefix
            :param allow_aggregation - allow aggregation of values into one update
            :return: gNMI update array
            """
            pass

        @abstractmethod
        def add_path_for_monitoring(self, path, prefix):
            """
            Add this path for monitoring for changes
            Monitoring must be stopped.
            :param path:
            :param prefix:
            :return:
            """
            pass

        @abstractmethod
        def get_monitored_changes(self) -> List:
            """
            Get gNMI subscription updates for changed values
            :return: gNMI update array
            #TODO should we also return delete array
            """
            pass

        @abstractmethod
        def start_monitoring(self):
            """
            Start monitoring for changes.
            This method should be non-blocking.
            :return:
            """
            pass

        @abstractmethod
        def stop_monitoring(self):
            """
            Stop monitoring changes
            (it must be started with start_monitoring)
            :return:
            """
            pass

        def is_once(self):
            """
            Return True if subscription is of such type that that only one sample
            is needed (no read thread)
            :return:
            """
            return self.subscription_list.mode == gnmi_pb2.SubscriptionList.ONCE

        def is_poll(self):
            """
            Return True if subscription is of such type (POLL) that more requests
            can be expected.
            :return:
            """
            return self.subscription_list.mode == gnmi_pb2.SubscriptionList.POLL

        def _is_monitor_changes(self):
            """
            Return True if subscription is of such type that we should monitor
            changes.
            :return:
            """
            is_monitor = self.subscription_list.mode == gnmi_pb2.SubscriptionList.STREAM and any(
                s.mode == gnmi_pb2.SubscriptionMode.ON_CHANGE for s in
                self.subscription_list.subscription)

            return is_monitor

        def put_event(self, event):
            """
            Put event to queue of `read` function.
            :param event:
            :return:
            """
            log.info("==> event=%s", event)
            assert self.subscription_list is not None
            assert self.read_queue is not None
            self.read_queue.put(event)
            log.info("<== ")

        def stop(self):
            """
            Stop processing of subscriptions.
            Sends SubscriptionEvent.FINISH to read function.
            """
            log.info("==>")
            if self.monitoring:
                self.stop_monitoring()
            self.put_event(self.SubscriptionEvent.FINISH)
            log.info("<==")

        def async_stop(self):
            """
            The change thread is aborting, the server thread needs to
            finalize it.
            """
            log.info("==>")
            self.put_event(self.SubscriptionEvent.ASYNC_FINISH)
            log.info("<==")

        def sample(self, subscriptions):
            """
            Get current sample of subscribed paths according to
            `subscriptions`.
            :param: start_monitoring: if True, the paths will be monitored
            for future changes
            TODO `delete` is processed and `delete` array is empty
            TODO timestamp is 0
            :return: SubscribeResponse with sample
            """
            log.debug("==> subscriptions=%s", subscriptions)
            update = []

            for s in subscriptions:
                update.extend(self.get_sample(path=s.path,
                                              prefix=self.subscription_list.prefix,
                                              allow_aggregation=self.subscription_list.allow_aggregation))
            notif = gnmi_pb2.Notification(timestamp=0,
                                          prefix=self.subscription_list.prefix,
                                          update=update,
                                          delete=[],
                                          atomic=False)
            response = gnmi_pb2.SubscribeResponse(update=notif)
            log.debug("<== response=%s", response)
            return response

        @staticmethod
        def sync_response():
            """
            create SubscribeResponse with  sync_response set to True
            :return: SubscribeResponse with sync_response set to True
            """
            log.debug("==> sync_response")
            response = gnmi_pb2.SubscribeResponse(sync_response=True)
            log.debug("<== response=%s", response)
            return response

        def changes(self):
            """
            Get subscription responses for changes (subscribed values).
            `update` arrays contain changes
            TODO `delete` is processed and `delete` array is empty
            TODO timestamp is 0
            :return: SubscribeResponse with changes
            """
            log.debug("==>")
            notifications = self.get_subscription_notifications()
            responses = [gnmi_pb2.SubscribeResponse(update=notif)
                         for notif in notifications]
            log.debug("<== responses=%s", responses)
            return responses

        def get_subscription_notifications(self):
            update = self.get_monitored_changes()
            notif = gnmi_pb2.Notification(timestamp=0,
                                          prefix=self.subscription_list.prefix,
                                          update=update,
                                          delete=[],
                                          atomic=False)
            return [notif]

        def _get_next_sample_interval_and_subscriptions(self,
                                                        first_sample_time: int):
            interval = None
            subscriptions = []
            curr = time.time_ns()
            for s in self.subscription_list.subscription:
                if s.mode == gnmi_pb2.SubscriptionMode.SAMPLE:
                    interval_mod = (
                                               curr - first_sample_time) % s.sample_interval
                    interval_candidate = s.sample_interval - interval_mod
                    if interval is None:
                        interval = interval_candidate
                    # todo some threshold ?
                    if interval == interval_candidate:
                        subscriptions.append(s)
                    elif interval > interval_candidate:
                        subscriptions = [s]
                        interval = interval_candidate

            log.debug("interval=%s", interval)
            return interval, subscriptions

        def read(self):
            """
            Read (get) subscription response(s) (in stream) for added
            subscription requests.
            This is generator function. For streaming subscription it contains
            event loop driven by self.read_queue (Queue object) and
            SubscriptionEvent messages.
            Response contains `notification` or `sync_response`.
            :return: nothing
            #TODO POLL mode with updates_only (send sync_response)
            :see: SubscriptionHandler.SubscriptionEvent
            """
            log.info("==>")
            # TODO exceptions
            assert self.subscription_list is not None
            if not self.is_once():
                assert self.read_queue is not None
            event = None
            first_sample = True
            first_sample_time = 0
            next_sample_interval = None
            sample_subscriptions = self.subscription_list.subscription

            for s in sample_subscriptions:
                if self.monitoring and s.mode == gnmi_pb2.SubscriptionMode.ON_CHANGE:
                    self.add_path_for_monitoring(s.path, self.subscription_list.prefix)

            while True:
                log.debug("Processing event type %s", event)
                # SAMPLE is handled in the same way as "first_sample"
                if first_sample or event == self.SubscriptionEvent.SAMPLE:
                    response = self.sample(sample_subscriptions)
                    if first_sample and self.monitoring:
                        self.start_monitoring()
                    yield response
                    if first_sample:
                        first_sample_time = time.time_ns()
                        yield self.sync_response()
                    first_sample = False
                    if self.is_once():
                        break
                    if self.subscription_list.mode == gnmi_pb2.SubscriptionList.STREAM:
                        (next_sample_interval, sample_subscriptions) = \
                            self._get_next_sample_interval_and_subscriptions(
                                first_sample_time)
                    if next_sample_interval:
                        # convert to seconds
                        next_sample_interval = float(next_sample_interval / 1000000000)
                elif event == self.SubscriptionEvent.FINISH:
                    log.debug("finishing subscription read")
                    break
                elif event == self.SubscriptionEvent.ASYNC_FINISH:
                    log.debug("subscription thread aborting")
                    if self.monitoring:
                        self.stop_monitoring()
                    break
                elif event == self.SubscriptionEvent.SEND_CHANGES:
                    response = self.changes()
                    log.debug("Sending changes")
                    yield from response
                elif event is None:
                    log.warning("**** event is None ! ****")
                    # TODO error
                    break
                else:
                    log.warning("**** event=%s not processed ! ****", event)
                    # TODO error
                    break
                log.debug("Waiting for event next_sample_interval=%s",
                          next_sample_interval)
                try:
                    event = self.read_queue.get(timeout=next_sample_interval)
                except Empty:
                    log.debug("sample timeout")
                    event = self.SubscriptionEvent.SAMPLE

                log.debug("Woke up event=%s", event)
            log.info("<==")

        def poll(self):
            """
            Poll (invoke SubscriptionEvent.SAMPLE in read) current state
            according to the subscription_list and add it to request stream.
            """
            log.info("==>")
            # TODO exception
            self.put_event(self.SubscriptionEvent.SAMPLE)
            log.info("<==")

    @abstractmethod
    def get_subscription_handler(self,
                                 subscription_list) -> SubscriptionHandler:
        pass

    @classmethod
    @abstractmethod
    def get_adapter(cls):
        """
        Get adapter instance
        We use this, since we want to have some adapter variants as singleton.
        :return: adapter instance
        """
        pass
