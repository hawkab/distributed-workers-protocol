import importlib.util
from pathlib import Path
import struct
import unittest
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('readiness_ci',Path(__file__).resolve().parents[1]/'fleetci.py');ci=importlib.util.module_from_spec(spec);spec.loader.exec_module(ci)
def frame(number,data=b''):
 payload=struct.pack('>HH',10,number)+data
 return struct.pack('>BHI',1,0,len(payload))+payload+b'\xce'
class Socket:
 def __init__(self,data):self.data=data;self.sent=[]
 def recv(self,n):value=self.data[:min(n,3)];self.data=self.data[len(value):];return value
 def sendall(self,data):self.sent.append(data)
 def __enter__(self):return self
 def __exit__(self,*args):pass
class ReadinessTests(unittest.TestCase):
 def test_actual_amqp_login_tune_and_vhost_open_are_required(self):
  socket=Socket(frame(10)+frame(30,struct.pack('>HIH',2047,131072,60))+frame(41)+frame(51))
  with patch.object(ci.socket,'create_connection',return_value=socket):ci.amqp_application_ready('amqp://test:fixture-password@example.invalid:5672/%2F')
  self.assertEqual(socket.sent[0],b'AMQP\0\0\x09\x01')
  self.assertIn(b'fixture-password',socket.sent[1]);self.assertEqual(len(socket.sent),5)
 def test_vm_ready_but_listener_not_ready_is_retried_without_attempt_cap(self):
  failures=[OSError('refused') for _ in range(25)]+[None]
  with patch.object(ci,'amqp_application_ready',side_effect=failures) as application,patch.object(ci.time,'sleep'):
   ci.wait_amqp_application('amqp://example.invalid/')
  self.assertEqual(application.call_count,26)
 def test_bad_auth_is_not_mislabeled_transient_startup(self):
  socket=Socket(frame(10)+frame(50,struct.pack('>H',403)))
  with patch.object(ci.socket,'create_connection',return_value=socket),self.assertRaisesRegex(ci.Failure,'authentication_rejected'):ci.amqp_application_ready('amqp://example.invalid/')
