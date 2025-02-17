import os
import random
import string
import time

from testbase.conf import settings
from testbase.testcase import TestCase

from src.polaris_test_lib.polaris import PolarisServer
from src.polaris_test_lib.polaris_testcase import PolarisTestCase


class SpringCloudTencentServiceCheck(PolarisTestCase):
    """
    Used to test spring cloud tencent services register and discovery.

    """
    owner = "atom"
    status = TestCase.EnumStatus.Ready
    priority = TestCase.EnumPriority.Normal
    timeout = 15

    def run_test(self):
        # ===========================
        self.get_console_token()
        self.polaris_server = PolarisServer(self.token, self.user_id)
        _random_str = ''.join(random.sample(string.ascii_letters + string.digits, 4))

        self.discovery_caller_port = random.randint(30000, 50000)
        self.discovery_callee1_port = random.randint(30000, 50000)
        self.discovery_callee2_port = random.randint(30000, 50000)
        # ===========================
        self.get_kona_jdk()
        self.get_spring_cloud_tencent_example()
        new_directory = self.create_temp_test_directory(temp_dir_suffix=_random_str, resource_name="kona-jdk")
        self.create_temp_test_directory(
            temp_dir_suffix=_random_str,
            resource_name="spring-cloud-tencent-demo/%s" % settings.POLARIS_TEST_SCT_EXAMPLE_VERSION,
            file_name="discovery-*.jar")
        # ===========================
        self.start_step(
            "Register by spring cloud tencent demo: discovery-callee/caller"
            "[https://github.com/Tencent/spring-cloud-tencent/tree/{version}/spring-cloud-tencent-examples/polaris-discovery-example].")
        reg_ip = settings.POLARIS_SERVER_ADDR
        srv_maps = {
            "DiscoveryCallerService": [
                {"srv_port": self.discovery_caller_port,
                 "srv_region_info": "south-china:ap-guangzhou:ap-guangzhou-1",
                 "jar_name": "discovery-caller-service"}
            ],
            # callee 与 caller 不处于就近地域
            "DiscoveryCalleeService": [
                {"srv_port": self.discovery_callee1_port,
                 "srv_region_info": "shanghai-zone-1:ap-shanghai:ap-shanghai-2",
                 "jar_name": "discovery-callee-service"},
                {"srv_port": self.discovery_callee2_port,
                 "srv_region_info": "shanghai-zone-1:ap-beijing:ap-beijing-3",
                 "jar_name": "discovery-callee-service"}
            ]
        }

        for srv, srv_info in srv_maps.items():
            self.log_info("Register spring cloud tencent %s example." % srv)
            for _srv in srv_info:
                _region_info = _srv["srv_region_info"].split(":")
                _srv_port = _srv["srv_port"]
                _srv_jar_name = _srv["jar_name"]
                date_now = time.strftime("%Y%m%d%H%M", time.localtime(int(time.time())))
                cmd_exe = "export SCT_METADATA_ZONE={zone} && " \
                          "export SCT_METADATA_REGION={region} && " \
                          "export SCT_METADATA_CAMPUS={campus} && " \
                          "cd {temp_dir} && nohup TencentKona-{kona_jdk_version}*/bin/java " \
                          "-Dspring.application.name={srv_name} " \
                          "-Dspring.cloud.polaris.stat.enabled=true " \
                          "-Dspring.cloud.polaris.stat.pushgateway.enabled=true " \
                          "-Dspring.cloud.polaris.stat.pushgateway.address={polaris_ip}:9091 " \
                          "-Dspring.cloud.polaris.address=grpc://{polaris_ip}:8091 " \
                          "-Dserver.port={srv_port} " \
                          "-jar {jar_name}*.jar " \
                          ">{jar_name}.{srv_port}.{date}.log 2>&1 &".format(
                    zone=_region_info[0], region=_region_info[1], campus=_region_info[2],
                    temp_dir=new_directory, kona_jdk_version=settings.POLARIS_TEST_SCT_KONA_JDK_VERSION,
                    polaris_ip=reg_ip, srv_port=_srv_port, srv_name=srv, jar_name=_srv_jar_name, date=date_now
                )
                if os.system(cmd_exe) != 0:
                    raise RuntimeError("Exec cmd: %s error!" % cmd_exe)
                else:
                    self.log_info("Exec cmd: %s success!" % cmd_exe)
        # ==================================
        self.start_step("Wait 60s for service start up...")
        success_list = []
        start = time.time()
        while len(success_list) < 3 and time.time() - start < 60:
            for srv_port in [self.discovery_caller_port, self.discovery_callee1_port, self.discovery_callee2_port]:
                if srv_port in success_list:
                    continue

                cmd_wait = "netstat -npl | grep %s" % srv_port
                if os.system(cmd_wait) != 0:
                    self.log_info("%s start up waiting..." % srv_port)
                    time.sleep(5)
                else:
                    self.log_info("%s start up success!" % srv_port)
                    success_list.append(srv_port)

        if len(success_list) < 3:
            raise RuntimeError("Start up failed!")
        time.sleep(60)
        # ===========================
        for srv, srv_info in srv_maps.items():
            self.start_step("Check create service %s." % srv)
            return_services = self.get_all_services(self.polaris_server)
            return_service_names = [srv["name"] for srv in return_services]
            self.log_info(str(return_service_names))
            self.assert_("Fail! No return except polaris service.", srv in return_service_names)

            # ===========================
            self.start_step("Check register service instance info.")
            describe_service_instance_url = "http://" + self.polaris_server_http_restful_api_addr + PolarisServer.INSTANCE_PATH
            rsp = self.polaris_server.describe_service_instance(describe_service_instance_url, limit=10, offset=0,
                                                                namespace_name="default", service_name=srv)
            instances = rsp.json().get("instances", None)
            self.assert_("Fail! No return except service instances.", len(instances) == len(srv_info))

            for _srv_info in srv_info:
                for ins in instances:
                    if ins["port"] == _srv_info["srv_port"]:
                        ins_location = "%s:%s:%s" % (ins["location"]["zone"], ins["location"]["region"],
                                                     ins["location"]["campus"])
                        self.assert_("Fail! No return except service instance.",
                                     ins_location == _srv_info["srv_region_info"])

        # ===========================
        self.start_step("Request sct consumer to check provider discovery and sct feign invoke.")

        cmd_curl = "curl -sv 'http://127.0.0.1:%s/discovery/service/caller/feign?value1=10101&value2=20202'" % self.discovery_caller_port
        self.req_and_check(
            srv_res_check_map={30303: {"discovery_callee": 1}},
            cmd_req_line=cmd_curl, all_req_num=5
        )
        # ===========================
        self.start_step("Request sct consumer to check provider discovery and sct rest invoke.")

        cmd_curl = "curl -sv 'http://127.0.0.1:%s/discovery/service/caller/rest'" % self.discovery_caller_port
        self.req_and_check(
            srv_res_check_map={self.discovery_callee1_port: {"discovery_callee1": 0.5},
                               self.discovery_callee2_port: {"discovery_callee2": 0.5}},
            cmd_req_line=cmd_curl, all_req_num=30
        )

    def post_test(self):
        # ===========================
        self.start_step("Stop all discovery services")
        for p in [self.discovery_caller_port, self.discovery_callee1_port, self.discovery_callee2_port]:
            cmd_kill = "ps axu | grep TencentKona | grep %s |grep -v grep | awk '{print $2}' | xargs kill -9 " % p
            os.system(cmd_kill)
        # ===========================
        self.start_step("Clean all discovery services")
        self.clean_test_services(self.polaris_server, service_name="DiscoveryCallerService")
        self.clean_test_services(self.polaris_server, service_name="DiscoveryCalleeService")
