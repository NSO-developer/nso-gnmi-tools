# -*- mode: python; python-indent: 4 -*-
import ncs

from confd_gnmi_common import PORT
from confd_gnmi_servicer import ConfDgNMIServicer, AdapterType


# ------------------------
# SERVICE CALLBACK EXAMPLE
# ------------------------

# ---------------------------------------------
# COMPONENT THREAD THAT WILL BE STARTED BY NCS.
# ---------------------------------------------
class Main(ncs.application.Application):
    def setup(self):
        # The application class sets up logging for us. It is accessible
        # through 'self.log' and is a ncs.log.Log instance.
        self.log.info('Main RUNNING')

        # Service callbacks require a registration for a 'service point',
        # as specified in the corresponding data model.
        #
        # self.register_service('nso-gnmi-tools-servicepoint', ServiceCallbacks)

        self.server = ConfDgNMIServicer.serve(PORT, AdapterType.API, insecure=True)
        self.log.info('gNMI server created')

        # If we registered any callback(s) above, the Application class
        # took care of creating a daemon (related to the service/action point).

        # When this setup method is finished, all registrations are
        # considered done and the application is 'started'.

    def teardown(self):
        # When the application is finished (which would happen if NCS went
        # down, packages were reloaded or some error occurred) this teardown
        # method will be called.

        self.server.stop()
        self.server.wait_for_termination()
        self.log.info('Main FINISHED')
