"""End-to-end test for Goose ACP in the OpenStack Assistant pod."""

import pytest

from rhos_lightspeed_tests.lib.acp import AcpClient, PodExecTransport


class TestOpenStackAssistant:
    @pytest.mark.openshift_dependent
    def test_goose_query(self, config):
        """Goose ACP answers an OpenStack question successfully from its pod."""
        assistant_pod = config["openstack_assistant_pod"]
        with PodExecTransport(
            pod_name=assistant_pod["name"],
            namespace=assistant_pod["namespace"],
            command=["goose", "acp"],
            openshift_config=config["openshift"],
        ) as transport:
            acp_client = AcpClient(transport)
            acp_client.initialize()
            session_id = acp_client.new_session()
            response = acp_client.prompt(session_id, "What is nova in OpenStack?")

        assert response.strip(), "Goose returned an empty response"
