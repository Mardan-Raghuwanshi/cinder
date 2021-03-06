
# Copyright IBM Corp. 2013 All Rights Reserved
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import mock
import os
import tempfile

from oslo.config import cfg

from cinder import context
from cinder import exception
from cinder.openstack.common import log as logging
from cinder.openstack.common import processutils
from cinder import test
from cinder import units
from cinder import utils
from cinder.volume import configuration as conf
from cinder.volume.drivers.ibm import gpfs
from cinder.volume import volume_types

from mock import patch

LOG = logging.getLogger(__name__)

CONF = cfg.CONF


class FakeQemuImgInfo(object):
    def __init__(self):
        self.file_format = None
        self.backing_file = None


class GPFSDriverTestCase(test.TestCase):
    driver_name = "cinder.volume.drivers.gpfs.GPFSDriver"
    context = context.get_admin_context()

    def _execute_wrapper(self, cmd, *args, **kwargs):
        try:
            kwargs.pop('run_as_root')
        except KeyError:
            pass

        return utils.execute(cmd, *args, **kwargs)

    def setUp(self):
        super(GPFSDriverTestCase, self).setUp()
        self.volumes_path = tempfile.mkdtemp(prefix="gpfs_")
        self.images_dir = '%s/images' % self.volumes_path

        if not os.path.exists(self.volumes_path):
            os.mkdir(self.volumes_path)
        if not os.path.exists(self.images_dir):
            os.mkdir(self.images_dir)
        self.image_id = '70a599e0-31e7-49b7-b260-868f441e862b'

        self.driver = gpfs.GPFSDriver(configuration=conf.Configuration(None))
        self.driver.set_execute(self._execute_wrapper)
        self.driver._cluster_id = '123456'
        self.driver._gpfs_device = '/dev/gpfs'
        self.driver._storage_pool = 'system'

        self.flags(volume_driver=self.driver_name,
                   gpfs_mount_point_base=self.volumes_path)

        self.context = context.get_admin_context()
        self.context.user_id = 'fake'
        self.context.project_id = 'fake'
        CONF.gpfs_images_dir = self.images_dir

    def tearDown(self):
        try:
            os.rmdir(self.images_dir)
            os.rmdir(self.volumes_path)
        except OSError:
            pass
        super(GPFSDriverTestCase, self).tearDown()

    def test_different(self):
        self.assertTrue(gpfs._different((True, False)))
        self.assertFalse(gpfs._different((True, True)))
        self.assertFalse(gpfs._different(None))

    def test_sizestr(self):
        self.assertEqual(gpfs._sizestr('0'), '100M')
        self.assertEqual(gpfs._sizestr('10'), '10G')

    @patch('cinder.utils.execute')
    def test_get_gpfs_state_ok(self, mock_exec):
        mock_exec.return_value = ('mmgetstate::HEADER:version:reserved:'
                                  'reserved:nodeName:nodeNumber:state:quorum:'
                                  'nodesUp:totalNodes:remarks:cnfsState:\n'
                                  'mmgetstate::0:1:::devstack:3:active:2:3:3:'
                                  'quorum node:(undefined):', '')
        self.assertEqual(True, self.driver._get_gpfs_state().splitlines()[1].
                         startswith('mmgetstate::0:1:::devstack'))

    @patch('cinder.utils.execute')
    def test_get_gpfs_state_fail_mmgetstate(self, mock_exec):
        mock_exec.side_effect = processutils.ProcessExecutionError(
            stdout='test', stderr='test')
        self.assertRaises(exception.VolumeBackendAPIException,
                          self.driver._get_gpfs_state)

    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._get_gpfs_state')
    def test_check_gpfs_state_ok(self, mock_get_gpfs_state):
        mock_get_gpfs_state.return_value = ('mmgetstate::HEADER:version:'
                                            'reserved:reserved:nodeName:'
                                            'nodeNumber:state:quorum:nodesUp:'
                                            'totalNodes:remarks:cnfsState:\n'
                                            'mmgetstate::0:1:::devstack:3:'
                                            'active:2:3:3:'
                                            'quorum node:(undefined):')
        self.driver._check_gpfs_state()

    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._get_gpfs_state')
    def test_check_gpfs_state_fail_not_active(self, mock_get_gpfs_state):
        mock_get_gpfs_state.return_value = ('mmgetstate::HEADER:version:'
                                            'reserved:reserved:nodeName:'
                                            'nodeNumber:state:quorum:nodesUp:'
                                            'totalNodes:remarks:cnfsState:\n'
                                            'mmgetstate::0:1:::devstack:3:'
                                            'arbitrating:2:3:3:'
                                            'quorum node:(undefined):')
        self.assertRaises(exception.VolumeBackendAPIException,
                          self.driver._check_gpfs_state)

    @patch('cinder.utils.execute')
    def test_get_fs_from_path_ok(self, mock_exec):
        mock_exec.return_value = ('Filesystem           1K-blocks      '
                                  'Used Available Use%% Mounted on\n'
                                  '%s             10485760    531968   9953792'
                                  '   6%% /gpfs0' % self.driver._gpfs_device,
                                  '')
        self.assertEqual(self.driver._gpfs_device,
                         self.driver._get_filesystem_from_path('/gpfs0'))

    @patch('cinder.utils.execute')
    def test_get_fs_from_path_fail_path(self, mock_exec):
        mock_exec.return_value = ('Filesystem           1K-blocks      '
                                  'Used Available Use% Mounted on\n'
                                  'test             10485760    531968   '
                                  '9953792   6% /gpfs0', '')
        self.assertNotEqual(self.driver._gpfs_device,
                            self.driver._get_filesystem_from_path('/gpfs0'))

    @patch('cinder.utils.execute')
    def test_get_fs_from_path_fail_raise(self, mock_exec):
        mock_exec.side_effect = processutils.ProcessExecutionError(
            stdout='test', stderr='test')
        self.assertRaises(exception.VolumeBackendAPIException,
                          self.driver._get_filesystem_from_path, '/gpfs0')

    @patch('cinder.utils.execute')
    def test_get_gpfs_cluster_id_ok(self, mock_exec):
        mock_exec.return_value = ('mmlsconfig::HEADER:version:reserved:'
                                  'reserved:configParameter:value:nodeList:\n'
                                  'mmlsconfig::0:1:::clusterId:%s::'
                                  % self.driver._cluster_id, '')
        self.assertEqual(self.driver._cluster_id,
                         self.driver._get_gpfs_cluster_id())

    @patch('cinder.utils.execute')
    def test_get_gpfs_cluster_id_fail_id(self, mock_exec):
        mock_exec.return_value = ('mmlsconfig::HEADER.:version:reserved:'
                                  'reserved:configParameter:value:nodeList:\n'
                                  'mmlsconfig::0:1:::clusterId:test::', '')
        self.assertNotEqual(self.driver._cluster_id,
                            self.driver._get_gpfs_cluster_id())

    @patch('cinder.utils.execute')
    def test_get_gpfs_cluster_id_fail_raise(self, mock_exec):
        mock_exec.side_effect = processutils.ProcessExecutionError(
            stdout='test', stderr='test')
        self.assertRaises(exception.VolumeBackendAPIException,
                          self.driver._get_gpfs_cluster_id)

    @patch('cinder.utils.execute')
    def test_get_fileset_from_path_ok(self, mock_exec):
        mock_exec.return_value = ('file name:            /gpfs0\n'
                                  'metadata replication: 1 max 2\n'
                                  'data replication:     1 max 2\n'
                                  'immutable:            no\n'
                                  'appendOnly:           no\n'
                                  'flags:\n'
                                  'storage pool name:    system\n'
                                  'fileset name:         root\n'
                                  'snapshot name:\n'
                                  'Windows attributes:   DIRECTORY', '')
        self.driver._get_fileset_from_path('')

    @patch('cinder.utils.execute')
    def test_get_fileset_from_path_fail_mmlsattr(self, mock_exec):
        mock_exec.side_effect = processutils.ProcessExecutionError(
            stdout='test', stderr='test')
        self.assertRaises(exception.VolumeBackendAPIException,
                          self.driver._get_fileset_from_path, '')

    @patch('cinder.utils.execute')
    def test_get_fileset_from_path_fail_find_fileset(self, mock_exec):
        mock_exec.return_value = ('file name:            /gpfs0\n'
                                  'metadata replication: 1 max 2\n'
                                  'data replication:     1 max 2\n'
                                  'immutable:            no\n'
                                  'appendOnly:           no\n'
                                  'flags:\n'
                                  'storage pool name:    system\n'
                                  '*** name:         root\n'
                                  'snapshot name:\n'
                                  'Windows attributes:   DIRECTORY', '')
        self.assertRaises(exception.VolumeBackendAPIException,
                          self.driver._get_fileset_from_path, '')

    @patch('cinder.utils.execute')
    def test_verify_gpfs_pool_ok(self, mock_exec):
        mock_exec.return_value = ('Storage pools in file system at \'/gpfs0\':'
                                  '\n'
                                  'Name                    Id   BlkSize Data '
                                  'Meta '
                                  'Total Data in (KB)   Free Data in (KB)   '
                                  'Total Meta in (KB)    Free Meta in (KB)\n'
                                  'system                   0    256 KB  yes  '
                                  'yes '
                                  '      10485760        9953792 ( 95%)       '
                                  '10485760        9954560 ( 95%)', '')
        self.assertTrue(self.driver._gpfs_device,
                        self.driver._verify_gpfs_pool('/dev/gpfs'))

    @patch('cinder.utils.execute')
    def test_verify_gpfs_pool_fail_pool(self, mock_exec):
        mock_exec.return_value = ('Storage pools in file system at \'/gpfs0\':'
                                  '\n'
                                  'Name                    Id   BlkSize Data '
                                  'Meta '
                                  'Total Data in (KB)   Free Data in (KB)   '
                                  'Total Meta in (KB)    Free Meta in (KB)\n'
                                  'test                   0    256 KB  yes  '
                                  'yes'
                                  '       10485760        9953792 ( 95%)'
                                  '       10485760        9954560 ( 95%)', '')
        self.assertTrue(self.driver._gpfs_device,
                        self.driver._verify_gpfs_pool('/dev/gpfs'))

    @patch('cinder.utils.execute')
    def test_verify_gpfs_pool_fail_raise(self, mock_exec):
        mock_exec.side_effect = processutils.ProcessExecutionError(
            stdout='test', stderr='test')
        self.assertFalse(self.driver._verify_gpfs_pool('/dev/gpfs'))

    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._verify_gpfs_pool')
    @patch('cinder.utils.execute')
    def test_update_volume_storage_pool_ok(self, mock_exec, mock_verify_pool):
        mock_verify_pool.return_value = True
        self.assertTrue(self.driver._update_volume_storage_pool('', 'system'))

    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._verify_gpfs_pool')
    @patch('cinder.utils.execute')
    def test_update_volume_storage_pool_ok_pool_none(self,
                                                     mock_exec,
                                                     mock_verify_pool):
        mock_verify_pool.return_value = True
        self.assertTrue(self.driver._update_volume_storage_pool('', None))

    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._verify_gpfs_pool')
    @patch('cinder.utils.execute')
    def test_update_volume_storage_pool_fail_pool(self,
                                                  mock_exec,
                                                  mock_verify_pool):
        mock_verify_pool.return_value = False
        self.assertRaises(exception.VolumeBackendAPIException,
                          self.driver._update_volume_storage_pool,
                          '',
                          'system')

    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._verify_gpfs_pool')
    @patch('cinder.utils.execute')
    def test_update_volume_storage_pool_fail_mmchattr(self,
                                                      mock_exec,
                                                      mock_verify_pool):
        mock_exec.side_effect = processutils.ProcessExecutionError(
            stdout='test', stderr='test')
        mock_verify_pool.return_value = True
        self.assertFalse(self.driver._update_volume_storage_pool('', 'system'))

    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver.'
           '_get_filesystem_from_path')
    @patch('cinder.utils.execute')
    def test_get_gpfs_fs_release_level_ok(self,
                                          mock_exec,
                                          mock_fs_from_path):
        mock_exec.return_value = ('mmlsfs::HEADER:version:reserved:reserved:'
                                  'deviceName:fieldName:data:remarks:\n'
                                  'mmlsfs::0:1:::gpfs:filesystemVersion:14.03 '
                                  '(4.1.0.0):\n'
                                  'mmlsfs::0:1:::gpfs:filesystemVersionLocal:'
                                  '14.03 (4.1.0.0):\n'
                                  'mmlsfs::0:1:::gpfs:filesystemVersionManager'
                                  ':14.03 (4.1.0.0):\n'
                                  'mmlsfs::0:1:::gpfs:filesystemVersion'
                                  'Original:14.03 (4.1.0.0):\n'
                                  'mmlsfs::0:1:::gpfs:filesystemHighest'
                                  'Supported:14.03 (4.1.0.0):', '')
        mock_fs_from_path.return_value = '/dev/gpfs'
        self.assertEqual(('/dev/gpfs', 1403),
                         self.driver._get_gpfs_fs_release_level(''))

    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver.'
           '_get_filesystem_from_path')
    @patch('cinder.utils.execute')
    def test_get_gpfs_fs_release_level_fail_mmlsfs(self,
                                                   mock_exec,
                                                   mock_fs_from_path):
        mock_exec.side_effect = processutils.ProcessExecutionError(
            stdout='test', stderr='test')
        mock_fs_from_path.return_value = '/dev/gpfs'
        self.assertRaises(exception.VolumeBackendAPIException,
                          self.driver._get_gpfs_fs_release_level, '')

    @patch('cinder.utils.execute')
    def test_get_gpfs_cluster_release_level_ok(self, mock_exec):
        mock_exec.return_value = ('mmlsconfig::HEADER:version:reserved:'
                                  'reserved:configParameter:value:nodeList:\n'
                                  'mmlsconfig::0:1:::minReleaseLevel:1403::',
                                  '')
        self.assertEqual(1403, self.driver._get_gpfs_cluster_release_level())

    @patch('cinder.utils.execute')
    def test_get_gpfs_cluster_release_level_fail_mmlsconfig(self, mock_exec):
        mock_exec.side_effect = processutils.ProcessExecutionError(
            stdout='test', stderr='test')
        self.assertRaises(exception.VolumeBackendAPIException,
                          self.driver._get_gpfs_cluster_release_level)

    @patch('cinder.utils.execute')
    def test_is_gpfs_path_fail_mmlsattr(self, mock_exec):
        mock_exec.side_effect = processutils.ProcessExecutionError(
            stdout='test', stderr='test')
        self.assertRaises(exception.VolumeBackendAPIException,
                          self.driver._is_gpfs_path, '/dummy/path')

    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._get_fileset_from_path')
    @patch('cinder.utils.execute')
    def test_is_same_fileset_ok(self,
                                mock_exec,
                                mock_get_fileset_from_path):
        mock_get_fileset_from_path.return_value = True
        self.assertTrue(self.driver._is_same_fileset('', ''))
        mock_get_fileset_from_path.side_effect = [True, False]
        self.assertFalse(self.driver._is_same_fileset('', ''))

    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._get_available_capacity')
    @patch('cinder.utils.execute')
    def test_same_cluster_ok(self, mock_exec, mock_avail_capacity):
        mock_avail_capacity.return_value = (10192683008, 10737418240)
        stats = self.driver.get_volume_stats()
        loc = stats['location_info']
        cap = {'location_info': loc}
        host = {'host': 'foo', 'capabilities': cap}
        self.assertTrue(self.driver._same_cluster(host))

        locinfo = stats['location_info'] + '_'
        loc = locinfo
        cap = {'location_info': loc}
        host = {'host': 'foo', 'capabilities': cap}
        self.assertFalse(self.driver._same_cluster(host))

    @patch('cinder.utils.execute')
    def test_set_rw_permission(self, mock_exec):
        self.driver._set_rw_permission('')

    @patch('cinder.utils.execute')
    def test_can_migrate_locally(self, mock_exec):
        host = {'host': 'foo', 'capabilities': ''}
        self.assertEqual(self.driver._can_migrate_locally(host), None)

        loc = 'GPFSDriver:%s' % self.driver._cluster_id
        cap = {'location_info': loc}
        host = {'host': 'foo', 'capabilities': cap}
        self.assertEqual(self.driver._can_migrate_locally(host), None)

        loc = 'GPFSDriver_:%s:testpath' % self.driver._cluster_id
        cap = {'location_info': loc}
        host = {'host': 'foo', 'capabilities': cap}
        self.assertEqual(self.driver._can_migrate_locally(host), None)

        loc = 'GPFSDriver:%s:testpath' % (self.driver._cluster_id + '_')
        cap = {'location_info': loc}
        host = {'host': 'foo', 'capabilities': cap}
        self.assertEqual(self.driver._can_migrate_locally(host), None)

        loc = 'GPFSDriver:%s:testpath' % self.driver._cluster_id
        cap = {'location_info': loc}
        host = {'host': 'foo', 'capabilities': cap}
        self.assertEqual(self.driver._can_migrate_locally(host), 'testpath')

    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._verify_gpfs_pool')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver.'
           '_get_filesystem_from_path')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._get_gpfs_cluster_id')
    @patch('cinder.utils.execute')
    def test_do_setup_ok(self,
                         mock_exec,
                         mock_get_gpfs_cluster_id,
                         mock_get_filesystem_from_path,
                         mock_verify_gpfs_pool):
        ctxt = self.context
        mock_get_gpfs_cluster_id.return_value = self.driver._cluster_id
        mock_get_filesystem_from_path.return_value = '/dev/gpfs'
        mock_verify_gpfs_pool.return_value = True
        self.driver.do_setup(ctxt)

    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._verify_gpfs_pool')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver.'
           '_get_filesystem_from_path')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._get_gpfs_cluster_id')
    @patch('cinder.utils.execute')
    def test_do_setup_fail_get_cluster_id(self,
                                          mock_exec,
                                          mock_get_gpfs_cluster_id,
                                          mock_get_filesystem_from_path,
                                          mock_verify_gpfs_pool):
        ctxt = self.context
        mock_get_gpfs_cluster_id.side_effect = (
            processutils.ProcessExecutionError(stdout='test', stderr='test'))
        mock_get_filesystem_from_path.return_value = '/dev/gpfs'
        mock_verify_gpfs_pool.return_value = True
        self.assertRaises(exception.VolumeBackendAPIException,
                          self.driver.do_setup, ctxt)

    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._verify_gpfs_pool')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver.'
           '_get_filesystem_from_path')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._get_gpfs_cluster_id')
    @patch('cinder.utils.execute')
    def test_do_setup_fail_get_fs_from_path(self,
                                            mock_exec,
                                            mock_get_gpfs_cluster_id,
                                            mock_get_fs_from_path,
                                            mock_verify_gpfs_pool):
        ctxt = self.context
        mock_get_gpfs_cluster_id.return_value = self.driver._cluster_id
        mock_get_fs_from_path.side_effect = (
            processutils.ProcessExecutionError(stdout='test', stderr='test'))
        mock_verify_gpfs_pool.return_value = True
        self.assertRaises(exception.VolumeBackendAPIException,
                          self.driver.do_setup, ctxt)

    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._verify_gpfs_pool')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver.'
           '_get_filesystem_from_path')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._get_gpfs_cluster_id')
    @patch('cinder.utils.execute')
    def test_do_setup_fail_volume(self,
                                  mock_exec,
                                  mock_get_gpfs_cluster_id,
                                  mock_get_filesystem_from_path,
                                  mock_verify_gpfs_pool):
        ctxt = self.context
        mock_get_gpfs_cluster_id. return_value = self.driver._cluster_id
        mock_get_filesystem_from_path.return_value = '/dev/gpfs'
        mock_verify_gpfs_pool.return_value = False
        self.assertRaises(exception.VolumeBackendAPIException,
                          self.driver.do_setup, ctxt)

    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._check_gpfs_state')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._is_gpfs_path')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver.'
           '_get_gpfs_fs_release_level')
    def test_check_for_setup_error_fail_conf(self,
                                             mock_get_gpfs_fs_rel_lev,
                                             mock_is_gpfs_path,
                                             mock_check_gpfs_state):
        fake_fs = '/dev/gpfs'
        fake_fs_release = 1400
        fake_cluster_release = 1201

        # fail configuration.gpfs_mount_point_base is None
        org_value = self.driver.configuration.gpfs_mount_point_base
        self.flags(volume_driver=self.driver_name, gpfs_mount_point_base=None)
        mock_get_gpfs_fs_rel_lev.return_value = (fake_fs, fake_fs_release)
        self.assertRaises(exception.VolumeBackendAPIException,
                          self.driver.check_for_setup_error)
        self.flags(volume_driver=self.driver_name,
                   gpfs_mount_point_base=org_value)

        # fail configuration.gpfs_images_share_mode not in
        # ['copy_on_write', 'copy']
        org_value = self.driver.configuration.gpfs_images_share_mode
        self.flags(volume_driver=self.driver_name,
                   gpfs_images_share_mode='copy_on_read')
        self.assertRaises(exception.VolumeBackendAPIException,
                          self.driver.check_for_setup_error)
        self.flags(volume_driver=self.driver_name,
                   gpfs_images_share_mode=org_value)

        # fail configuration.gpfs_images_share_mode and
        # configuration.gpfs_images_dir is None
        org_value_share_mode = self.driver.configuration.gpfs_images_share_mode
        self.flags(volume_driver=self.driver_name,
                   gpfs_images_share_mode='copy')
        org_value_dir = CONF.gpfs_images_dir
        CONF.gpfs_images_dir = None
        self.assertRaises(exception.VolumeBackendAPIException,
                          self.driver.check_for_setup_error)
        self.flags(volume_driver=self.driver_name,
                   gpfs_images_share_mode=org_value_share_mode)
        CONF.gpfs_images_dir = org_value_dir

        # fail configuration.gpfs_images_share_mode == 'copy_on_write' and not
        # _same_filesystem(configuration.gpfs_mount_point_base,
        # configuration.gpfs_images_dir)
        org_value = self.driver.configuration.gpfs_images_share_mode
        self.flags(volume_driver=self.driver_name,
                   gpfs_images_share_mode='copy_on_write')
        with mock.patch('cinder.volume.drivers.ibm.gpfs._same_filesystem',
                        return_value=False):
            self.assertRaises(exception.VolumeBackendAPIException,
                              self.driver.check_for_setup_error)
        self.flags(volume_driver=self.driver_name,
                   gpfs_images_share_mode=org_value)

        # fail self.configuration.gpfs_images_share_mode == 'copy_on_write' and
        # not self._is_same_fileset(self.configuration.gpfs_mount_point_base,
        # self.configuration.gpfs_images_dir)
        org_value = self.driver.configuration.gpfs_images_share_mode
        self.flags(volume_driver=self.driver_name,
                   gpfs_images_share_mode='copy_on_write')
        with mock.patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver.'
                        '_is_same_fileset', return_value=False):
            self.assertRaises(exception.VolumeBackendAPIException,
                              self.driver.check_for_setup_error)
        self.flags(volume_driver=self.driver_name,
                   gpfs_images_share_mode=org_value)

        # fail directory is None
        org_value_share_mode = self.driver.configuration.gpfs_images_share_mode
        self.flags(volume_driver=self.driver_name,
                   gpfs_images_share_mode=None)
        org_value_dir = CONF.gpfs_images_dir
        CONF.gpfs_images_dir = None
        with mock.patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver.'
                        '_get_gpfs_cluster_release_level',
                        return_value=fake_cluster_release):
            self.driver.check_for_setup_error()
        self.flags(volume_driver=self.driver_name,
                   gpfs_images_share_mode=org_value_share_mode)
        CONF.gpfs_images_dir = org_value_dir

         # fail directory.startswith('/')
        org_value_mount = self.driver.configuration.gpfs_mount_point_base
        self.flags(volume_driver=self.driver_name,
                   gpfs_mount_point_base='_' + self.volumes_path)
        org_value_dir = CONF.gpfs_images_dir
        CONF.gpfs_images_dir = None
        with mock.patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver.'
                        '_get_gpfs_cluster_release_level',
                        return_value=fake_cluster_release):
            self.assertRaises(exception.VolumeBackendAPIException,
                              self.driver.check_for_setup_error)
        self.flags(volume_driver=self.driver_name,
                   gpfs_mount_point_base=org_value_mount)
        CONF.gpfs_images_dir = org_value_dir

         # fail os.path.isdir(directory)
        org_value_mount = self.driver.configuration.gpfs_mount_point_base
        self.flags(volume_driver=self.driver_name,
                   gpfs_mount_point_base=self.volumes_path + '_')
        org_value_dir = CONF.gpfs_images_dir
        CONF.gpfs_images_dir = None
        with mock.patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver.'
                        '_get_gpfs_cluster_release_level',
                        return_value=fake_cluster_release):
            self.assertRaises(exception.VolumeBackendAPIException,
                              self.driver.check_for_setup_error)
        self.flags(volume_driver=self.driver_name,
                   gpfs_mount_point_base=org_value_mount)
        CONF.gpfs_images_dir = org_value_dir

        # fail not cluster release level >= GPFS_CLONE_MIN_RELEASE
        org_fake_cluster_release = fake_cluster_release
        fake_cluster_release = 1105
        org_value_mount = self.driver.configuration.gpfs_mount_point_base
        self.flags(volume_driver=self.driver_name,
                   gpfs_mount_point_base=self.volumes_path)
        org_value_dir = CONF.gpfs_images_dir
        CONF.gpfs_images_dir = None
        with mock.patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver.'
                        '_get_gpfs_cluster_release_level',
                        return_value=fake_cluster_release):
            with mock.patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver.'
                            '_get_gpfs_fs_release_level',
                            return_value=(fake_fs, fake_fs_release)):
                self.assertRaises(exception.VolumeBackendAPIException,
                                  self.driver.check_for_setup_error)
        fake_cluster_release = org_fake_cluster_release

        # fail not fs release level >= GPFS_CLONE_MIN_RELEASE
        org_fake_fs_release = fake_fs_release
        fake_fs_release = 1105
        org_value_mount = self.driver.configuration.gpfs_mount_point_base
        self.flags(volume_driver=self.driver_name,
                   gpfs_mount_point_base=self.volumes_path)
        org_value_dir = CONF.gpfs_images_dir
        CONF.gpfs_images_dir = None
        with mock.patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver.'
                        '_get_gpfs_cluster_release_level',
                        return_value=fake_cluster_release):
            with mock.patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver.'
                            '_get_gpfs_fs_release_level',
                            return_value=(fake_fs, fake_fs_release)):
                self.assertRaises(exception.VolumeBackendAPIException,
                                  self.driver.check_for_setup_error)
        self.flags(volume_driver=self.driver_name,
                   gpfs_mount_point_base=org_value_mount)
        CONF.gpfs_images_dir = org_value_dir
        fake_fs_release = org_fake_fs_release

    @patch('cinder.utils.execute')
    def test_create_sparse_file(self, mock_exec):
        self.driver._create_sparse_file('', 100)

    @patch('cinder.utils.execute')
    def test_allocate_file_blocks(self, mock_exec):
        self.driver._allocate_file_blocks(os.path.join(self.images_dir,
                                                       'test'), 1)

    @patch('cinder.utils.execute')
    def test_gpfs_change_attributes(self, mock_exec):
        options = []
        options.extend(['-T', 'test'])
        self.driver._gpfs_change_attributes(options, self.images_dir)

    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._gpfs_change_attributes')
    def test_set_volume_attributes(self, mock_change_attributes):
        metadata = [dict([('key', 'data_pool_name'), ('value', 'test')]),
                    dict([('key', 'replicas'), ('value', 'test')]),
                    dict([('key', 'dio'), ('value', 'test')]),
                    dict([('key', 'write_affinity_depth'), ('value', 'test')]),
                    dict([('key', 'block_group_factor'), ('value', 'test')]),
                    dict([('key', 'write_affinity_failure_group'),
                          ('value', 'test')]),
                    dict([('key', 'test'),
                          ('value', 'test')])]
        self.driver._set_volume_attributes('', metadata)

    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._gpfs_change_attributes')
    def test_set_volume_attributes_no_attributes(self, mock_change_attributes):
        metadata = []
        org_value = self.driver.configuration.gpfs_storage_pool
        self.flags(volume_driver=self.driver_name, gpfs_storage_pool='system')
        self.driver._set_volume_attributes('', metadata)
        self.flags(volume_driver=self.driver_name,
                   gpfs_storage_pool=org_value)

    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._gpfs_change_attributes')
    def test_set_volume_attributes_no_options(self, mock_change_attributes):
        metadata = []
        org_value = self.driver.configuration.gpfs_storage_pool
        self.flags(volume_driver=self.driver_name, gpfs_storage_pool='')
        self.driver._set_volume_attributes('', metadata)
        self.flags(volume_driver=self.driver_name,
                   gpfs_storage_pool=org_value)

    @patch('cinder.utils.execute')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._allocate_file_blocks')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._set_volume_attributes')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._set_rw_permission')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._create_sparse_file')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver.local_path')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._verify_gpfs_path_state')
    def test_create_volume(self,
                           mock_gpfs_path_state,
                           mock_local_path,
                           mock_sparse_file,
                           mock_rw_permission,
                           mock_set_volume_attributes,
                           mock_allocate_file_blocks,
                           mock_exec):
        mock_local_path.return_value = 'test'
        volume = {}
        volume['size'] = 1000
        value = {}
        value['value'] = 'test'
        volume['volume_metadata'] = [dict([('key', 'fstype'),
                                           ('value', 'test')]),
                                     dict([('key', 'fslabel'),
                                           ('value', 'test')]),
                                     dict([('key', 'test'),
                                           ('value', 'test')])]

        org_value = self.driver.configuration.gpfs_sparse_volumes
        self.flags(volume_driver=self.driver_name, gpfs_sparse_volumes=False)
        self.driver.create_volume(volume)
        self.flags(volume_driver=self.driver_name,
                   gpfs_sparse_volumes=org_value)

    @patch('cinder.utils.execute')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._allocate_file_blocks')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._set_volume_attributes')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._set_rw_permission')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._create_sparse_file')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver.local_path')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._verify_gpfs_path_state')
    def test_create_volume_no_sparse_volume(self,
                                            mock_gpfs_path_state,
                                            mock_local_path,
                                            mock_sparse_file,
                                            mock_rw_permission,
                                            mock_set_volume_attributes,
                                            mock_allocate_file_blocks,
                                            mock_exec):
        mock_local_path.return_value = 'test'
        volume = {}
        volume['size'] = 1000
        value = {}
        value['value'] = 'test'
        volume['volume_metadata'] = [dict([('key', 'fstype_'),
                                           ('value', 'test')]),
                                     dict([('key', 'fslabel'),
                                           ('value', 'test')]),
                                     dict([('key', 'test'),
                                           ('value', 'test')])]

        org_value = self.driver.configuration.gpfs_sparse_volumes
        self.flags(volume_driver=self.driver_name, gpfs_sparse_volumes=True)
        self.driver.create_volume(volume)
        self.flags(volume_driver=self.driver_name,
                   gpfs_sparse_volumes=org_value)

    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._resize_volume_file')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._gpfs_redirect')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._set_rw_permission')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._create_gpfs_copy')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver.local_path')
    def test_create_volume_from_snapshot(self,
                                         mock_local_path,
                                         mock_create_gpfs_copy,
                                         mock_rw_permission,
                                         mock_gpfs_redirect,
                                         mock_resize_volume_file):
        mock_resize_volume_file.return_value = 5 * units.GiB
        volume = {}
        volume['size'] = 1000
        self.assertEqual(self.driver.create_volume_from_snapshot(volume, ''),
                         {'size': 5.0})

    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._resize_volume_file')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._set_rw_permission')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._create_gpfs_clone')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver.local_path')
    def test_create_cloned_volume(self,
                                  mock_local_path,
                                  mock_create_gpfs_clone,
                                  mock_rw_permission,
                                  mock_resize_volume_file):
        mock_resize_volume_file.return_value = 5 * units.GiB
        volume = {}
        volume['size'] = 1000
        self.assertEqual(self.driver.create_cloned_volume(volume, ''),
                         {'size': 5.0})

    @patch('cinder.utils.execute')
    def test_delete_gpfs_file_ok(self, mock_exec):
        mock_exec.side_effect = [('Parent  Depth   Parent inode   File name\n'
                                  '------  -----  --------------  ---------\n'
                                  '    no      2          148488  '
                                  '/gpfs0/test.txt', ''),
                                 ('', ''),
                                 ('', '')]
        self.driver._delete_gpfs_file(self.images_dir)
        self.driver._delete_gpfs_file(self.images_dir + '_')

        mock_exec.side_effect = [('Parent  Depth   Parent inode   File name\n'
                                  '------  -----  --------------  ---------\n'
                                  '                               '
                                  '/gpfs0/test.txt', ''),
                                 ('', '')]
        self.driver._delete_gpfs_file(self.images_dir)

    @patch('os.path.exists')
    @patch('cinder.utils.execute')
    def test_delete_gpfs_file_ok_parent(self, mock_exec, mock_path_exists):
        mock_path_exists.side_effect = [True, False, False,
                                        True, False, False,
                                        True, False, False]
        mock_exec.side_effect = [('Parent  Depth   Parent inode   File name\n'
                                  '------  -----  --------------  ---------\n'
                                  '    no      2          148488  '
                                  '/gpfs0/test.txt', ''),
                                 ('/gpfs0/test.snap\ntest', ''),
                                 ('', '')]
        self.driver._delete_gpfs_file(self.images_dir)
        mock_exec.side_effect = [('Parent  Depth   Parent inode   File name\n'
                                  '------  -----  --------------  ---------\n'
                                  '    no      2          148488  '
                                  '/gpfs0/test.txt', ''),
                                 ('/gpfs0/test.ts\ntest', ''),
                                 ('', '')]
        self.driver._delete_gpfs_file(self.images_dir)
        mock_exec.side_effect = [('Parent  Depth   Parent inode   File name\n'
                                  '------  -----  --------------  ---------\n'
                                  '    no      2          148488  '
                                  '/gpfs0/test.txt', ''),
                                 ('/gpfs0/test.txt\ntest', ''),
                                 ('', '')]
        self.driver._delete_gpfs_file(self.images_dir)

    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._delete_gpfs_file')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver.local_path')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._verify_gpfs_path_state')
    def test_delete_volume(self,
                           mock_verify_gpfs_path_state,
                           mock_local_path,
                           mock_delete_gpfs_file):
        self.driver.delete_volume('')

    @patch('cinder.utils.execute')
    def test_gpfs_redirect_ok(self, mock_exec):
        org_value = self.driver.configuration.gpfs_max_clone_depth
        self.flags(volume_driver=self.driver_name, gpfs_max_clone_depth=1)
        mock_exec.side_effect = [('Parent  Depth   Parent inode   File name\n'
                                  '------  -----  --------------  ---------\n'
                                  '    no      2          148488  '
                                  '/gpfs0/test.txt', ''),
                                 ('', '')]
        self.assertEqual(True, self.driver._gpfs_redirect(''))
        self.flags(volume_driver=self.driver_name, gpfs_max_clone_depth=1)
        mock_exec.side_effect = [('Parent  Depth   Parent inode   File name\n'
                                  '------  -----  --------------  ---------\n'
                                  '    no      1          148488  '
                                  '/gpfs0/test.txt', ''),
                                 ('', '')]
        self.assertEqual(False, self.driver._gpfs_redirect(''))
        self.flags(volume_driver=self.driver_name,
                   gpfs_max_clone_depth=org_value)

    @patch('cinder.utils.execute')
    def test_gpfs_redirect_fail_depth(self, mock_exec):
        org_value = self.driver.configuration.gpfs_max_clone_depth
        self.flags(volume_driver=self.driver_name, gpfs_max_clone_depth=0)
        mock_exec.side_effect = [('Parent  Depth   Parent inode   File name\n'
                                  '------  -----  --------------  ---------\n'
                                  '    no      2          148488  '
                                  '/gpfs0/test.txt', ''),
                                 ('', '')]
        self.assertEqual(False, self.driver._gpfs_redirect(''))
        self.flags(volume_driver=self.driver_name,
                   gpfs_max_clone_depth=org_value)

    @patch('cinder.utils.execute')
    def test_gpfs_redirect_fail_match(self, mock_exec):
        org_value = self.driver.configuration.gpfs_max_clone_depth
        self.flags(volume_driver=self.driver_name, gpfs_max_clone_depth=1)
        mock_exec.side_effect = [('Parent  Depth   Parent inode   File name\n'
                                  '------  -----  --------------  ---------\n'
                                  '                       148488  '
                                  '/gpfs0/test.txt', ''),
                                 ('', '')]
        self.assertEqual(False, self.driver._gpfs_redirect(''))
        self.flags(volume_driver=self.driver_name,
                   gpfs_max_clone_depth=org_value)

    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._create_gpfs_snap')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._create_gpfs_copy')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._gpfs_redirect')
    @patch('cinder.utils.execute')
    def test_create_gpfs_clone(self,
                               mock_exec,
                               mock_redirect,
                               mock_cr_gpfs_cp,
                               mock_cr_gpfs_snap):
        mock_redirect.return_value = True
        self.driver._create_gpfs_clone('', '')
        mock_redirect.side_effect = [True, False]
        self.driver._create_gpfs_clone('', '')

    @patch('cinder.utils.execute')
    def test_create_gpfs_copy(self, mock_exec):
        self.driver._create_gpfs_copy('', '')

    @patch('cinder.utils.execute')
    def test_create_gpfs_snap(self, mock_exec):
        self.driver._create_gpfs_snap('')
        self.driver._create_gpfs_snap('', '')

    @patch('cinder.utils.execute')
    def test_is_gpfs_parent_file_ok(self, mock_exec):
        mock_exec.side_effect = [('Parent  Depth   Parent inode   File name\n'
                                  '------  -----  --------------  ---------\n'
                                  '   yes      2          148488  '
                                  '/gpfs0/test.txt', ''),
                                 ('Parent  Depth   Parent inode   File name\n'
                                  '------  -----  --------------  ---------\n'
                                  '    no      2          148488  '
                                  '/gpfs0/test.txt', '')]
        self.assertEqual(True, self.driver._is_gpfs_parent_file(''))
        self.assertEqual(False, self.driver._is_gpfs_parent_file(''))

    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._gpfs_redirect')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._set_rw_permission')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._create_gpfs_snap')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver.local_path')
    def test_create_snapshot(self,
                             mock_local_path,
                             mock_create_gpfs_snap,
                             mock_set_rw_permission,
                             mock_gpfs_redirect):
        org_value = self.driver.configuration.gpfs_mount_point_base
        self.flags(volume_driver=self.driver_name,
                   gpfs_mount_point_base=self.volumes_path)
        snapshot = {}
        snapshot['volume_name'] = 'test'
        self.driver.create_snapshot(snapshot)
        self.flags(volume_driver=self.driver_name,
                   gpfs_mount_point_base=org_value)

    @patch('cinder.utils.execute')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver.local_path')
    def test_delete_snapshot(self,
                             mock_local_path,
                             mock_exec):
        snapshot = {}
        self.driver.delete_snapshot(snapshot)

    def test_ensure_export(self):
        self.assertEqual(None, self.driver.ensure_export('', ''))

    def test_create_export(self):
        self.assertEqual(None, self.driver.create_export('', ''))

    def test_remove_export(self):
        self.assertEqual(None, self.driver.remove_export('', ''))

    def test_initialize_connection(self):
        volume = {}
        volume['name'] = 'test'
        data = self.driver.initialize_connection(volume, '')
        self.assertEqual(data['data']['name'], 'test')
        self.assertEqual(data['data']['device_path'], os.path.join(
            self.driver.configuration.gpfs_mount_point_base, 'test'))

    def test_terminate_connection(self):
        self.assertEqual(None, self.driver.terminate_connection('', ''))

    def test_get_volume_stats(self):
        fake_avail = 80 * units.GiB
        fake_size = 2 * fake_avail
        with mock.patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver.'
                        '_get_available_capacity',
                        return_value=(fake_avail, fake_size)):
            stats = self.driver.get_volume_stats()
            self.assertEqual(stats['volume_backend_name'], 'GPFS')
            self.assertEqual(stats['storage_protocol'], 'file')
            stats = self.driver.get_volume_stats(True)
            self.assertEqual(stats['volume_backend_name'], 'GPFS')
            self.assertEqual(stats['storage_protocol'], 'file')

    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._update_volume_stats')
    def test_get_volume_stats_none_stats(self, mock_upd_vol_stats):
        _stats_org = self.driver._stats
        self.driver._stats = mock.Mock()
        self.driver._stats.return_value = None
        self.driver.get_volume_stats()
        self.driver._stats = _stats_org

    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._clone_image')
    def test_clone_image_pub(self, mock_exec):
        self.driver.clone_image('', '', '', '')

    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._is_gpfs_path')
    def test_is_cloneable_ok(self, mock_is_gpfs_path):
        self.flags(volume_driver=self.driver_name,
                   gpfs_images_share_mode='test')
        CONF.gpfs_images_dir = self.images_dir
        mock_is_gpfs_path.return_value = None
        self.assertEqual((True, None, os.path.join(CONF.gpfs_images_dir,
                                                   '12345')),
                         self.driver._is_cloneable('12345'))

    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._is_gpfs_path')
    def test_is_cloneable_fail_config(self, mock_is_gpfs_path):
        self.flags(volume_driver=self.driver_name, gpfs_images_share_mode='')
        CONF.gpfs_images_dir = ''
        mock_is_gpfs_path.return_value = None
        self.assertNotEqual((True, None, os.path.join(CONF.gpfs_images_dir,
                                                      '12345')),
                            self.driver._is_cloneable('12345'))

    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._is_gpfs_path')
    def test_is_cloneable_fail_path(self, mock_is_gpfs_path):
        self.flags(volume_driver=self.driver_name,
                   gpfs_images_share_mode='test')
        CONF.gpfs_images_dir = self.images_dir
        mock_is_gpfs_path.side_effect = (
            processutils.ProcessExecutionError(stdout='test', stderr='test'))
        self.assertNotEqual((True, None, os.path.join(CONF.gpfs_images_dir,
                                                      '12345')),
                            self.driver._is_cloneable('12345'))

    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._resize_volume_file')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._set_rw_permission')
    @patch('cinder.image.image_utils.convert_image')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._create_gpfs_copy')
    @patch('cinder.image.image_utils.qemu_img_info')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._create_gpfs_snap')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._is_gpfs_parent_file')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver.local_path')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._is_cloneable')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._verify_gpfs_path_state')
    def test_clone_image(self,
                         mock_verify_gpfs_path_state,
                         mock_is_cloneable,
                         mock_local_path,
                         mock_is_gpfs_parent_file,
                         mock_create_gpfs_snap,
                         mock_qemu_img_info,
                         mock_create_gpfs_copy,
                         mock_conv_image,
                         mock_set_rw_permission,
                         mock_resize_volume_file):
        mock_is_cloneable.return_value = (True, 'test', self.images_dir)
        mock_is_gpfs_parent_file.return_value = False
        mock_qemu_img_info.return_value = self._fake_qemu_qcow2_image_info('')
        volume = {}
        volume['id'] = 'test'
        volume['size'] = 1000
        self.assertEqual(({'provider_location': None}, True),
                         self.driver._clone_image(volume, '', 1))

    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._is_cloneable')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._verify_gpfs_path_state')
    def test_clone_image_not_cloneable(self,
                                       mock_verify_gpfs_path_state,
                                       mock_is_cloneable):
        mock_is_cloneable.return_value = (False, 'test', self.images_dir)
        volume = {}
        volume['id'] = 'test'
        volume['size'] = 1000
        self.assertEqual((None, False),
                         self.driver._clone_image(volume, '', 1))

    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._resize_volume_file')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._set_rw_permission')
    @patch('cinder.image.image_utils.convert_image')
    @patch('shutil.copyfile')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._create_gpfs_copy')
    @patch('cinder.image.image_utils.qemu_img_info')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._create_gpfs_snap')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._is_gpfs_parent_file')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver.local_path')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._is_cloneable')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._verify_gpfs_path_state')
    def test_clone_image_format(self,
                                mock_verify_gpfs_path_state,
                                mock_is_cloneable,
                                mock_local_path,
                                mock_is_gpfs_parent_file,
                                mock_create_gpfs_snap,
                                mock_qemu_img_info,
                                mock_create_gpfs_copy,
                                mock_copyfile,
                                mock_conv_image,
                                mock_set_rw_permission,
                                mock_resize_volume_file):
        mock_is_cloneable.return_value = (True, 'test', self.images_dir)
        mock_is_gpfs_parent_file.return_value = True
        mock_qemu_img_info.return_value = self._fake_qemu_raw_image_info('')
        volume = {}
        volume['id'] = 'test'
        volume['size'] = 1000
        org_value = self.driver.configuration.gpfs_images_share_mode
        self.flags(volume_driver=self.driver_name,
                   gpfs_images_share_mode='copy_on_write')
        self.assertEqual(({'provider_location': None}, True),
                         self.driver._clone_image(volume, '', 1))

        self.flags(volume_driver=self.driver_name,
                   gpfs_images_share_mode='copy')
        self.assertEqual(({'provider_location': None}, True),
                         self.driver._clone_image(volume, '', 1))

        self.flags(volume_driver=self.driver_name,
                   gpfs_images_share_mode='copy_on_read')
        self.assertEqual(({'provider_location': None}, True),
                         self.driver._clone_image(volume, '', 1))
        self.flags(volume_driver=self.driver_name,
                   gpfs_images_share_mode=org_value)

    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._resize_volume_file')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver.local_path')
    @patch('cinder.image.image_utils.fetch_to_raw')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._verify_gpfs_path_state')
    def test_copy_image_to_volume(self,
                                  mock_verify_gpfs_path_state,
                                  mock_fetch_to_raw,
                                  mock_local_path,
                                  mock_resize_volume_file):
        volume = {}
        volume['id'] = 'test'
        volume['size'] = 1000
        self.driver.copy_image_to_volume('', volume, '', 1)

    @patch('cinder.image.image_utils.qemu_img_info')
    @patch('cinder.image.image_utils.resize_image')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver.local_path')
    def test_resize_volume_file_ok(self,
                                   mock_local_path,
                                   mock_resize_image,
                                   mock_qemu_img_info):
        volume = {}
        volume['id'] = 'test'
        mock_qemu_img_info.return_value = self._fake_qemu_qcow2_image_info('')
        self.assertEqual(self._fake_qemu_qcow2_image_info('').virtual_size,
                         self.driver._resize_volume_file(volume, 2000))

    @patch('cinder.image.image_utils.qemu_img_info')
    @patch('cinder.image.image_utils.resize_image')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver.local_path')
    def test_resize_volume_file_fail(self,
                                     mock_local_path,
                                     mock_resize_image,
                                     mock_qemu_img_info):
        volume = {}
        volume['id'] = 'test'
        mock_resize_image.side_effect = (
            processutils.ProcessExecutionError(stdout='test', stderr='test'))
        mock_qemu_img_info.return_value = self._fake_qemu_qcow2_image_info('')
        self.assertRaises(exception.VolumeBackendAPIException,
                          self.driver._resize_volume_file, volume, 2000)

    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._resize_volume_file')
    def test_extend_volume(self, mock_resize_volume_file):
        volume = {}
        volume['id'] = 'test'
        self.driver.extend_volume(volume, 2000)

    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver.local_path')
    @patch('cinder.image.image_utils.upload_volume')
    def test_copy_volume_to_image(self, mock_upload_volume, mock_local_path):
        volume = {}
        volume['id'] = 'test'
        self.driver.copy_volume_to_image('', volume, '', '')

    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._delete_gpfs_file')
    @patch('cinder.openstack.common.fileutils.file_open')
    @patch('cinder.utils.temporary_chown')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._gpfs_redirect')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._create_gpfs_clone')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver.local_path')
    def test_backup_volume(self,
                           mock_local_path,
                           mock_create_gpfs_clone,
                           mock_gpfs_redirect,
                           mock_temp_chown,
                           mock_file_open,
                           mock_delete_gpfs_file):
        volume = {}
        volume['name'] = 'test'
        self.driver.db = mock.Mock()
        self.driver.db.volume_get = mock.Mock()
        self.driver.db.volume_get.return_value = volume
        backup = {}
        backup['volume_id'] = 'test'
        backup['id'] = '123456'
        backup_service = mock.Mock()
        mock_local_path.return_value = self.volumes_path
        self.driver.backup_volume('', backup, backup_service)

    @patch('cinder.openstack.common.fileutils.file_open')
    @patch('cinder.utils.temporary_chown')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver.local_path')
    def test_restore_backup(self,
                            mock_local_path,
                            mock_temp_chown,
                            mock_file_open):
        volume = {}
        volume['id'] = '123456'
        backup = {}
        backup['id'] = '123456'
        backup_service = mock.Mock()
        mock_local_path.return_value = self.volumes_path
        self.driver.restore_backup('', backup, volume, backup_service)

    @patch('cinder.utils.execute')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._can_migrate_locally')
    def test_migrate_volume_ok(self, mock_local, mock_exec):
        volume = {}
        volume['name'] = 'test'
        host = {}
        host = {'host': 'foo', 'capabilities': {}}
        mock_local.return_value = (self.driver.configuration.
                                   gpfs_mount_point_base + '_')
        self.assertEqual((True, None),
                         self.driver._migrate_volume(volume, host))

    @patch('cinder.utils.execute')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._can_migrate_locally')
    def test_migrate_volume_fail_dest_path(self, mock_local, mock_exec):
        volume = {}
        volume['name'] = 'test'
        host = {}
        host = {'host': 'foo', 'capabilities': {}}
        mock_local.return_value = None
        self.assertEqual((False, None),
                         self.driver._migrate_volume(volume, host))

    @patch('cinder.utils.execute')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._can_migrate_locally')
    def test_migrate_volume_fail_mpb(self, mock_local, mock_exec):
        volume = {}
        volume['name'] = 'test'
        host = {}
        host = {'host': 'foo', 'capabilities': {}}
        mock_local.return_value = (self.driver.configuration.
                                   gpfs_mount_point_base)
        mock_exec.side_effect = (
            processutils.ProcessExecutionError(stdout='test', stderr='test'))
        self.assertEqual((True, None),
                         self.driver._migrate_volume(volume, host))

    @patch('cinder.utils.execute')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._can_migrate_locally')
    def test_migrate_volume_fail_mv(self, mock_local, mock_exec):
        volume = {}
        volume['name'] = 'test'
        host = {}
        host = {'host': 'foo', 'capabilities': {}}
        mock_local.return_value = (
            self.driver.configuration.gpfs_mount_point_base + '_')
        mock_exec.side_effect = (
            processutils.ProcessExecutionError(stdout='test', stderr='test'))
        self.assertEqual((False, None),
                         self.driver._migrate_volume(volume, host))

    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._migrate_volume')
    def test_migrate_volume_ok_pub(self, mock_migrate_volume):
        self.driver.migrate_volume('', '', '')

    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._migrate_volume')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver.'
           '_update_volume_storage_pool')
    @patch('cinder.volume.drivers.ibm.gpfs._different')
    def test_retype_ok(self, mock_different, mock_strg_pool, mock_migrate_vol):
        ctxt = self.context
        (volume, new_type, diff, host) = self._fake_retype_arguments()
        self.driver.db = mock.Mock()
        mock_different.side_effect = [False, True, True]
        mock_strg_pool.return_value = True
        mock_migrate_vol.return_value = (True, True)
        self.assertTrue(self.driver.retype(ctxt, volume, new_type, diff, host))

    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._migrate_volume')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver.'
           '_update_volume_storage_pool')
    @patch('cinder.volume.drivers.ibm.gpfs._different')
    def test_retype_diff_backend(self,
                                 mock_different,
                                 mock_strg_pool,
                                 mock_migrate_vol):
        ctxt = self.context
        (volume, new_type, diff, host) = self._fake_retype_arguments()
        mock_different.side_effect = [True, True, True]
        self.assertFalse(self.driver.retype(ctxt,
                                            volume,
                                            new_type,
                                            diff, host))

    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._migrate_volume')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver.'
           '_update_volume_storage_pool')
    @patch('cinder.volume.drivers.ibm.gpfs._different')
    def test_retype_diff_pools_migrated(self,
                                        mock_different,
                                        mock_strg_pool,
                                        mock_migrate_vol):
        ctxt = self.context
        (volume, new_type, diff, host) = self._fake_retype_arguments()
        self.driver.db = mock.Mock()
        mock_different.side_effect = [False, False, True]
        mock_strg_pool.return_value = True
        mock_migrate_vol.return_value = (True, True)
        self.assertTrue(self.driver.retype(ctxt, volume, new_type, diff, host))

    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._migrate_volume')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver.'
           '_update_volume_storage_pool')
    @patch('cinder.volume.drivers.ibm.gpfs._different')
    def test_retype_diff_pools(self,
                               mock_different,
                               mock_strg_pool,
                               mock_migrate_vol):
        ctxt = self.context
        (volume, new_type, diff, host) = self._fake_retype_arguments()
        mock_different.side_effect = [False, False, True]
        mock_strg_pool.return_value = True
        mock_migrate_vol.return_value = (False, False)
        self.assertFalse(self.driver.retype(ctxt,
                                            volume,
                                            new_type,
                                            diff,
                                            host))

    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._migrate_volume')
    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver.'
           '_update_volume_storage_pool')
    @patch('cinder.volume.drivers.ibm.gpfs._different')
    def test_retype_no_diff_hit(self,
                                mock_different,
                                mock_strg_pool,
                                mock_migrate_vol):
        ctxt = self.context
        (volume, new_type, diff, host) = self._fake_retype_arguments()
        mock_different.side_effect = [False, False, False]
        self.assertFalse(self.driver.retype(ctxt,
                                            volume,
                                            new_type,
                                            diff,
                                            host))

    @patch('cinder.utils.execute')
    def test_mkfs_ok(self, mock_exec):
        volume = {}
        volume['name'] = 'test'
        self.driver._mkfs(volume, 'swap')
        self.driver._mkfs(volume, 'swap', 'test')
        self.driver._mkfs(volume, 'ext3', 'test')
        self.driver._mkfs(volume, 'vfat', 'test')

    @patch('cinder.utils.execute')
    def test_mkfs_fail_mk(self, mock_exec):
        volume = {}
        volume['name'] = 'test'
        mock_exec.side_effect = (
            processutils.ProcessExecutionError(stdout='test', stderr='test'))
        self.assertRaises(exception.VolumeBackendAPIException,
                          self.driver._mkfs, volume, 'swap', 'test')

    @patch('cinder.utils.execute')
    def test_get_available_capacity_ok(self, mock_exec):
        mock_exec.return_value = ('Filesystem         1-blocks      Used '
                                  'Available Capacity Mounted on\n'
                                  '/dev/gpfs            10737418240 544735232 '
                                  '10192683008       6%% /gpfs0', '')
        self.assertEqual((10192683008, 10737418240),
                         self.driver._get_available_capacity('/gpfs0'))

    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._verify_gpfs_path_state')
    @patch('cinder.utils.execute')
    def test_get_available_capacity_fail_mounted(self,
                                                 mock_exec,
                                                 mock_path_state):
        mock_path_state.side_effect = (
            exception.VolumeBackendAPIException('test'))
        mock_exec.return_value = ('Filesystem         1-blocks      Used '
                                  'Available Capacity Mounted on\n'
                                  '/dev/gpfs            10737418240 544735232 '
                                  '10192683008       6%% /gpfs0', '')
        self.assertEqual((0, 0), self.driver._get_available_capacity('/gpfs0'))

    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._is_gpfs_path')
    def test_verify_gpfs_path_state_ok(self, mock_is_gpfs_path):
        self.driver._verify_gpfs_path_state(self.images_dir)

    @patch('cinder.volume.drivers.ibm.gpfs.GPFSDriver._is_gpfs_path')
    def test_verify_gpfs_path_state_fail_path(self, mock_is_gpfs_path):
        mock_is_gpfs_path.side_effect = (
            processutils.ProcessExecutionError(stdout='test', stderr='test'))
        self.assertRaises(exception.VolumeBackendAPIException,
                          self.driver._verify_gpfs_path_state, self.images_dir)

    def _fake_qemu_qcow2_image_info(self, path):
        data = FakeQemuImgInfo()
        data.file_format = 'qcow2'
        data.backing_file = None
        data.virtual_size = 1 * units.GiB
        return data

    def _fake_qemu_raw_image_info(self, path):
        data = FakeQemuImgInfo()
        data.file_format = 'raw'
        data.backing_file = None
        data.virtual_size = 1 * units.GiB
        return data

    def _fake_retype_arguments(self):
        ctxt = self.context
        loc = 'GPFSDriver:%s:testpath' % self.driver._cluster_id
        cap = {'location_info': loc}
        host = {'host': 'foo', 'capabilities': cap}
        key_specs_old = {'capabilities:storage_pool': 'bronze',
                         'volume_backend_name': 'backend1'}
        key_specs_new = {'capabilities:storage_pool': 'gold',
                         'volume_backend_name': 'backend1'}
        old_type_ref = volume_types.create(ctxt, 'old', key_specs_old)
        new_type_ref = volume_types.create(ctxt, 'new', key_specs_new)

        volume_types.get_volume_type(ctxt, old_type_ref['id'])
        new_type = volume_types.get_volume_type(ctxt, new_type_ref['id'])

        diff, equal = volume_types.volume_types_diff(ctxt,
                                                     old_type_ref['id'],
                                                     new_type_ref['id'])

        volume = {}
        volume['name'] = 'test'
        volume['host'] = host
        volume['id'] = '123456'

        return (volume, new_type, diff, host)
