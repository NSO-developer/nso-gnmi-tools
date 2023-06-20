# -*- mode: python; python-indent: 4 -*-
import os
import sys
from socket import socket, AF_INET, SOCK_STREAM

import ncs
import _ncs
import _ncs.cdb as cdb

from confd_gnmi_common import PORT
from confd_gnmi_servicer import ConfDgNMIServicer, AdapterType

# ---------------------------------------------
# COMPONENT THREAD THAT WILL BE STARTED BY NCS.
# ---------------------------------------------

class GnmiIter(object):
    def __init__(self, restart_fun):
        self.restart_fun = restart_fun

    def pre_iterate(self):
        self.restart_fun()
        return []

    def iterate(self, kp, op, oldv, newv, state):
        return ncs.ITER_STOP

    def post_iterate(self, state):
        pass

    def should_post_iterate(self, state):
        return False

class Main(ncs.application.Application):

    def start_gnmi(self):
        self.log.info('=> start_gnmi')

        insecure = True
        key_file = None
        crt_file = None
        port = PORT

        # read configuration from CDB
        rsock = socket(AF_INET, SOCK_STREAM, 0)

        cdb.connect(rsock, cdb.READ_SOCKET, '127.0.0.1', _ncs.PORT)
        cdb.start_session(rsock, cdb.RUNNING)
        port = cdb.get(rsock, "/nso-gnmi-tools/port")
        self.log.info(f"enabled cdb.get={cdb.get(rsock, '/nso-gnmi-tools/tls/enabled')}")
        insecure = cdb.get(rsock, "/nso-gnmi-tools/tls/enabled") != "true"
        self.log.info(f"insecure={insecure}")
        if not insecure:
            if cdb.exists(rsock, "/nso-gnmi-tools/tls/keyFile"):
                # TODO
                key_file = cdb.get(rsock, "/nso-gnmi-tools/tls/keyFile")
            if cdb.exists(rsock, "/nso-gnmi-tools/tls/certFile"):
                # TODO
                crt_file = cdb.get(rsock, "/nso-gnmi-tools/tls/certFile")


        cdb.close(rsock)

        self.server = ConfDgNMIServicer.serve(port, AdapterType.API,
                                              insecure=insecure,
                                              key_file=key_file,
                                              crt_file=crt_file)

        self.log.info('<= start_gnmi')

    def stop_gnmi(self):
        self.log.info('=> stop_gnmi')

        if hasattr(self,"server") and self.server is not None:
            self.server.stop(1)
            self.log.info('wait for gnmi termination')
            self.server.wait_for_termination()
            self.server = None

        self.log.info('<= stop_gnmi')

    def restart_gnmi(self):
        self.log.info('=> restart_gnmi')
        self.stop_gnmi()
        self.start_gnmi()

        self.log.info('<= restart_gnmi')

    def setup(self):
        self.log.info('=> setup')

        sys.path.append(os.getenv('NCS_DIR') + "/src/ncs/pyapi/ncs")
        self.sub = ncs.cdb.Subscriber()
        self.sub.register("/nso-gnmi-tools", iter_obj=GnmiIter(
            restart_fun=self.restart_gnmi))
        self.sub.start()

        self.start_gnmi()

        self.log.info('<= setup')

    def teardown(self):
        # When the application is finished (which would happen if NCS went
        # down, packages were reloaded or some error occurred) this teardown
        # method will be called.
        self.log.info('teardown - calling stop ')
        self.stop_gnmi()
        self.sub.stop()
        self.log.info('Main FINISHED')
