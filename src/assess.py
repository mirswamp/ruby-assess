import os
import re
import uuid
import shutil
import logging
import os.path as osp
import xml.etree.ElementTree as ET
import subprocess

from . import gencmd
from . import utillib
from . import confreader
from .logger import LogTaskStatus
from .utillib import UnpackArchiveError


class ToolInstallFailedError(Exception):

    def __init__(self, value):
        Exception.__init__(self)
        self.value = value

    def __str__(self):
        return repr(self.value)


class BuildArtifactsError(Exception):

    def __init__(self, value):
        Exception.__init__(self)
        self.value = value

    def __str__(self):
        return repr(self.value)


class BuildSummaryError(Exception):

    def __init__(self, field, filename):
        Exception.__init__(self)
        self.value = "No `{0}` tag found in `{1}` file".format(field, filename)

    def __str__(self):
        return repr(self.value)


class BuildArtifactsHelper:

    @classmethod
    def _get_fileset(cls, build_root_dir, xml_elem):
        #fileset = set()
        fileset = list()

        for _file in xml_elem:
            if not osp.isabs(_file.text):
                fileset.append(osp.join(build_root_dir, _file.text))
            else:
                fileset.append(_file.text)
        #return list(fileset)
        return fileset

    @classmethod
    def get_artifacts(cls, _id, build_summary, xml_elem):
        artifacts = dict(build_summary)
        artifacts['id'] = _id

        for elem in xml_elem:
            if elem.tag in ['include', 'exclude', 'dependency']:
                fileset = BuildArtifactsHelper._get_fileset(artifacts['build-root-dir'], elem)
                artifacts[elem.tag] = fileset

                if elem.tag =='include':
                    artifacts['srcfile'] = fileset
                
        return artifacts

    @classmethod
    def _get_build_summary(cls, root):
        '''returns a dictionary'''
        return {elem.tag:elem.text for elem in root \
                if(elem.tag not in ['package-conf',
                                    'command', 'build-artifacts',
                                    'gem-install', 'gem-unpack',
                                    'build-command'])}

    def __init__(self, build_summary_file):

        root = ET.parse(build_summary_file).getroot()

        if root.tag != 'build-summary':
            raise BuildSummaryError('build-summary', build_summary_file)

        if root.find('exit-code') is None:
            raise BuildSummaryError('exit-code', build_summary_file)
        elif int(root.find('exit-code').text) != 0:
            raise BuildArtifactsError('exit-code not 0 in ' + build_summary_file)

        if root.find('build-root-dir') is None:
            raise BuildSummaryError('build-root-dir', build_summary_file)

        if root.find('build-artifacts') is None:
            raise BuildArtifactsError("No  Source Files or Class Files to Assess! "
                                      "Looks like no files with 'rb' extension were found.")

        self._build_summary = BuildArtifactsHelper._get_build_summary(root)
        self._build_artifacts = root.find('build-artifacts')
        self._package_conf = {elem.tag:elem.text for elem in root.find('package-conf')}

    def __contains__(self, key):
        return True if(key in self._build_summary) else False

    def __getitem__(self, key):
        if key in self._build_summary:
            return self._build_summary[key]
        else:
            return self._package_conf.get(key, None)

    def get_pkg_conf(self):
        return self._package_conf

    def get_build_artifacts(self, *args):
        ''' this is a generator function
        parses through the xml elements in the tree and
        yeilds objects artifacts that we are interested in provided as a parameter'''

        count = 1

        for elem in self._build_artifacts:
            if (elem.tag == 'ruby-src') and (elem.tag in args):
                yield BuildArtifactsHelper.get_artifacts(count,
                                                         self._build_summary,
                                                         elem)

            elif (elem.tag == 'no-build') and (elem.tag in args):
                raise NotImplementedError

            count += 1


class AssessmentSummary:

    def __init__(self,
                 filename,
                 build_artifacts_helper,
                 tool_conf):

        self._filename = filename

        ## keep the same name as other frameworks for consistency
        ## why ruby-assess is wildly different is ??
        build_summary_obj = build_artifacts_helper;

        self._root = ET.Element('assessment-summary')
        AssessmentSummary._add(self._root, 'assessment-summary-uuid', str(uuid.uuid4()))

        if 'build-fw' in build_summary_obj:
            AssessmentSummary._add(self._root, 'assess-fw', build_summary_obj['build-fw'])

        if 'build-fw-version' in build_summary_obj:
            AssessmentSummary._add(self._root, 'assess-fw-version', build_summary_obj['build-fw-version'])


        AssessmentSummary._add(self._root, 'build-root-dir',
                               build_artifacts_helper['build-root-dir'])

        AssessmentSummary._add(self._root, 'package-root-dir',
                               osp.join(build_artifacts_helper['build-root-dir'],
                                        build_artifacts_helper['package-root-dir']))

        AssessmentSummary._add(self._root, 'package-name',
                               build_artifacts_helper.get_pkg_conf().get('package-short-name'))

        AssessmentSummary._add(self._root, 'package-version',
                               build_artifacts_helper.get_pkg_conf().get('package-version'))

        if 'build-summary-uuid' in build_artifacts_helper:
            AssessmentSummary._add(self._root, 'build-summary-uuid',
                                   build_artifacts_helper['build-summary-uuid'])

        AssessmentSummary._add(self._root, 'tool-type', tool_conf['tool-type'])
        AssessmentSummary._add(self._root, 'tool-version', tool_conf['tool-version'])
        AssessmentSummary._add(self._root, 'platform-name', utillib.platform())
        AssessmentSummary._add(self._root, 'start-ts', utillib.posix_epoch())
        self._assessment_artifacts = AssessmentSummary._add(self._root, 'assessment-artifacts')

    def __enter__(self):
        return self

    @classmethod
    def _add(cls, parent, tag, text=None):
        elem = ET.SubElement(parent, tag)
        if text:
            elem.text = text
        return elem

    def __exit__(self, exception_type, value, traceback):
        AssessmentSummary._add(self._root, 'stop-ts', utillib.posix_epoch())

        tree = ET.ElementTree(self._root)
        tree.write(self._filename, encoding='UTF-8', xml_declaration=True)

    def add_non_assessment(self, build_artifact_id, cmd, exit_code,
                           execution_successful, environ, cwd, report, stdout, stderr,
                           starttime, endtime):

        non_assess_elem = AssessmentSummary._add(self._assessment_artifacts, 'non-assessment')

        if build_artifact_id:
            AssessmentSummary._add(non_assess_elem, 'build-artifact-id',
                                   str(build_artifact_id) if isinstance(build_artifact_id, int)
                                   else build_artifact_id)
        if osp.isfile(stdout):
            AssessmentSummary._add(non_assess_elem, 'stdout', osp.basename(stdout))
        if osp.isfile(stderr):
            AssessmentSummary._add(non_assess_elem, 'stderr', osp.basename(stderr))
        AssessmentSummary._add(non_assess_elem, 'exit-code', str(exit_code))
        AssessmentSummary._add(non_assess_elem, 'execution-successful',
                utillib.bool_to_string(execution_successful))
        AssessmentSummary._add(non_assess_elem, 'start-ts', starttime)
        AssessmentSummary._add(non_assess_elem, 'stop-ts', endtime)

        cmd_elem = AssessmentSummary._add(non_assess_elem, 'command')

        AssessmentSummary._add(cmd_elem, 'cwd', cwd)
        env_elem = AssessmentSummary._add(cmd_elem, 'environment')
        for key in environ.keys():
            AssessmentSummary._add(env_elem, 'env', '{0}={1}'.format(key, environ[key]))

        AssessmentSummary._add(cmd_elem, 'executable', cmd[0])
        args_elem = AssessmentSummary._add(cmd_elem, 'args')
        for arg in cmd:
            AssessmentSummary._add(args_elem, 'arg', arg)

    def add_report(self, build_artifact_id, cmd, exit_code,
                   execution_successful, environ, cwd, report, stdout,
                   stderr, starttime, endtime):

        assess_elem = AssessmentSummary._add(self._assessment_artifacts, 'assessment')
        if build_artifact_id:
            AssessmentSummary._add(assess_elem, 'build-artifact-id',
                                   str(build_artifact_id) if isinstance(build_artifact_id, int) \
                                       else build_artifact_id)

        AssessmentSummary._add(assess_elem, 'report', osp.basename(report))
        AssessmentSummary._add(assess_elem, 'stdout', osp.basename(stdout))
        AssessmentSummary._add(assess_elem, 'stderr', osp.basename(stderr))
        AssessmentSummary._add(assess_elem, 'exit-code', str(exit_code))
        AssessmentSummary._add(assess_elem, 'execution-successful',
                utillib.bool_to_string(execution_successful))
        AssessmentSummary._add(assess_elem, 'start-ts', starttime)
        AssessmentSummary._add(assess_elem, 'stop-ts', endtime)

        cmd_elem = AssessmentSummary._add(assess_elem, 'command')

        AssessmentSummary._add(cmd_elem, 'cwd', cwd)
        env_elem = AssessmentSummary._add(cmd_elem, 'environment')
        for key in environ.keys():
            AssessmentSummary._add(env_elem, 'env', '{0}={1}'.format(key, environ[key]))

        AssessmentSummary._add(cmd_elem, 'executable', cmd[0])
        args_elem = AssessmentSummary._add(cmd_elem, 'args')
        for arg in cmd:
            AssessmentSummary._add(args_elem, 'arg', arg)


class SwaTool:

    TOOL_DOT_CONF = 'tool.conf'

    @classmethod
    def get_services_conf(cls, tool_type, input_root_dir):
        
        conf_file = osp.join(input_root_dir, 'services.conf')
        if osp.isfile(conf_file):
            services_conf = confreader.read_conf_into_dict(conf_file)
            return {key: services_conf[key] for key in services_conf.keys() if key.startswith('tool-' + tool_type)}
        else:
            return dict()

    def __init__(self, input_root_dir, output_root_dir, tool_root_dir):

        tool_conf_file = osp.join(input_root_dir, SwaTool.TOOL_DOT_CONF)
        self.tool_root_dir = tool_root_dir
        self.input_root_dir = input_root_dir

        tool_conf = confreader.read_conf_into_dict(tool_conf_file)

        if 'tool-defaults' in tool_conf:
            tool_defaults_file = osp.join(input_root_dir, tool_conf['tool-defaults'])
            self._tool_conf = confreader.read_conf_into_dict(tool_defaults_file)
            self._tool_conf.update(tool_conf)
        else:
            self._tool_conf = tool_conf
        
        self._tool_conf.update(SwaTool.get_services_conf(self._tool_conf['tool-type'], input_root_dir))
            
        self._tool_conf = {key: utillib.expandvar(self._tool_conf[key], self._tool_conf) \
                           for key in self._tool_conf}

        self._unarchive(input_root_dir, tool_root_dir)
        logging.info('TOOL CONF: %s', self._tool_conf)
        self._install(tool_root_dir, output_root_dir) 

    def _unarchive(self, input_root_dir, tool_root_dir):

        with LogTaskStatus('tool-unarchive'):
            tool_archive = osp.join(input_root_dir, self._tool_conf['tool-archive'])
            exit_code = utillib.unpack_archive(tool_archive, tool_root_dir)

            if exit_code != 0:
                raise UnpackArchiveError(self._tool_conf['tool-archive'])

    def _get_env(self):
        gem_user_dir = subprocess.getoutput("ruby -r rubygems -e 'puts Gem.user_dir'")
        #logging.info('GEM USER DIR: %s', gem_user_dir)
        new_env = dict(os.environ)
        new_env['PATH'] = '%s/bin:%s' % (gem_user_dir, new_env.get('PATH', ''))
        new_env['GEM_HOME'] = '%s' % (gem_user_dir)
        new_env['GEM_PATH'] = '%s' % (gem_user_dir)
        #new_env['GEM_PATH'] = '%s:%s' % (gem_user_dir, new_env.get('GEM_PATH', ''))
        return new_env

    def _install(self, tool_root_dir, output_root_dir):

        with LogTaskStatus('tool-install') as status_dot_out:

            if 'tool-install-cmd' not in self._tool_conf:
                self._tool_conf['executable'] = osp.normpath(osp.join(osp.join(tool_root_dir,
                                                                               self._tool_conf['tool-dir']),
                                                                      self._tool_conf['executable']))

                status_dot_out.skip_task()
            else:
                # Some packages have tool installed as part of their dependencies
                # If the versions of those tools are latest and greatest than SWAMP tools,
                # Those tools get used. So first uninstall tools if installed

                #uninstall_cmd = 'gem uninstall %s --all' % (self._tool_conf['tool-type'])
                #logging.info('TOOL UNINSTALL COMMAND: %s', uninstall_cmd)
                #utillib.run_cmd(uninstall_cmd)

                #install_cmd = self._tool_conf['tool-install-cmd'].split()
                #install_cmd = ['gem', 'install', '--no-document']
                #gemfile = '{0}-{1}.gem'.format(self._tool_conf['tool-type'],
                #                               self._tool_conf['tool-version'])
                #install_cmd.append(osp.join(self._tool_conf['tool-dir'], gemfile))
                
                install_cmd = self._tool_conf['tool-install-cmd']
                logging.info('TOOL INSTALL COMMAND: %s', install_cmd)
                exit_code, environ = utillib.run_cmd(install_cmd,
                                                     outfile=osp.join(output_root_dir, 'tool_install.out'),
                                                     errfile=osp.join(output_root_dir, 'tool_install.err'),
                                                     cwd=osp.join(tool_root_dir,
                                                                  self._tool_conf['tool-dir']),
                                                     env=self._get_env())
                logging.info('TOOL INSTALL ENVIRON: %s', environ)

                if exit_code != 0:
                    raise ToolInstallFailedError("Install Tool Failed, "
                                                 "Command '{0}' return {1}".format(install_cmd, exit_code))

    def _validate_exit_code(self, exit_code):
        if 'valid-exit-status' in self._tool_conf:
            regex = re.compile(self._tool_conf['valid-exit-status'])
            return True if(regex.match(str(exit_code))) else False
        else:
            return True if(exit_code == 0) else False

    def _read_err_msg(self, exit_code, outfile, errfile):
        err_msg = ''
 
        if ('tool-report-exit-code' in self._tool_conf) and \
           ('tool-report-exit-code-msg' in self._tool_conf) and \
           (exit_code == int(self._tool_conf['tool-report-exit-code'])):

            if osp.isfile(errfile):
                err_msg_regex = re.compile(self._tool_conf['tool-report-exit-code-msg'])
                line_num = 1
                with open(errfile) as fobj:
                    for line in fobj:
                        if err_msg_regex.search(line.strip()):
                            err_msg += '{0}:{1}: {2}\n'.format('/'.join(errfile.split('/')[-2:]),
                                                               line_num, line.strip())
                        line_num += 1
        return err_msg
    
    def assess(self, build_summary_file, results_root_dir):
        raise NotImplementedError

    
class RubyLint(SwaTool):

    def __init__(self, input_root_dir, output_root_dir, tool_root_dir):
        SwaTool.__init__(self, input_root_dir, output_root_dir, tool_root_dir)

    def create_config_file(self, dependencies):
        config_file = osp.join(self.tool_root_dir, 'ruby-lint.yml')

        with open(config_file, 'w') as fobj:
            print('---', file=fobj)
            print('directories:', file=fobj)
            for filepath in dependencies:
                print(' - %s' % (filepath,), file=fobj)

        return config_file

    @classmethod
    def _has_runtime_errors(cls, errfile):
        if osp.getsize(errfile):
            with open(errfile, 'r') as fobj:
                line1 = fobj.readline().strip()
                return True if line1.endswith('(RuntimeError)') else False

    def assess(self, build_summary_file, results_root_dir):

        if not osp.isdir(results_root_dir):
            os.makedirs(results_root_dir, exist_ok=True)

        assessment_summary_file = osp.join(results_root_dir, 'assessment_summary.xml')

        build_artifacts_helper = BuildArtifactsHelper(build_summary_file)

        with AssessmentSummary(assessment_summary_file,
                               build_artifacts_helper,
                               self._tool_conf) as assessment_summary:
            passed = 0
            failed = 0

            for artifacts in build_artifacts_helper.get_build_artifacts('ruby-src'):

                skip_assess = 'include' not in artifacts or len(artifacts['include']) == 0
                if not skip_assess:
                    assess_cmd_template = [self._tool_conf['executable'],
                                           '--presenter',
                                           'syntastic']

                    if 'dependency' in artifacts:
                        config_file = self.create_config_file(artifacts['dependency'])
                        assess_cmd_template.extend(['--config', config_file])

                    file_count = 0

                    for srcfile in artifacts['include']:

                        assess_cmd = list(assess_cmd_template)

                        assess_cmd.append(srcfile)

                        logging.info('ASSESSMENT CMD: %s', assess_cmd)

                        file_count += 1
                        artifacts_id = '{0}-{1}'.format(artifacts['id'], file_count)
                        outfile = osp.join(results_root_dir, 'assessment_report{0}.out'.format(artifacts_id))
                        errfile = osp.join(results_root_dir, 'swa_tool_stderr{0}.out'.format(artifacts_id))

                        start_time = utillib.posix_epoch()
                        exit_code, environ = utillib.run_cmd(assess_cmd,
                                                             outfile=outfile,
                                                             errfile=errfile,
                                                             cwd=results_root_dir,
                                                             #env=dict(os.environ))
                                                             env=self._get_env())
                        end_time = utillib.posix_epoch()

                        logging.info('ASSESSMENT WORKING DIR: %s', results_root_dir)
                        logging.info('ASSESSMENT EXIT CODE: %d', exit_code)
                        logging.info('ASSESSMENT ENVIRONMENT: %s', environ)

                        if self._validate_exit_code(exit_code) and \
                                not RubyLint._has_runtime_errors(errfile):
                            passed += 1
                            execution_successful = True
                        else:
                            failed += 1
                            execution_successful = False

                        #write assessment summary file
                        #return pass, fail, assessment_summary
                        assessment_summary.add_report(artifacts_id,
                                                      assess_cmd,
                                                      exit_code,
                                                      execution_successful,
                                                      environ,
                                                      results_root_dir,
                                                      outfile,
                                                      outfile,
                                                      errfile,
                                                      start_time,
                                                      end_time)

            return (passed, failed, '', assessment_summary_file)


class RubyTool(SwaTool):

    def __init__(self, input_root_dir, output_root_dir, tool_root_dir):
        SwaTool.__init__(self, input_root_dir, output_root_dir, tool_root_dir)

    def assess(self, build_summary_file, results_root_dir):

        if not osp.isdir(results_root_dir):
            os.makedirs(results_root_dir, exist_ok=True)

        assessment_summary_file = osp.join(results_root_dir, 'assessment_summary.xml')

        if 'assessment-report-template' in self._tool_conf:
            assessment_report_template = self._tool_conf['assessment-report-template']
        else:
            assessment_report_template = 'assessment_report{0}.xml'

        build_artifacts_helper = BuildArtifactsHelper(build_summary_file)

        passed = 0
        failed = 0
        err_msgs = ''

        with AssessmentSummary(assessment_summary_file,
                               build_artifacts_helper,
                               self._tool_conf) as assessment_summary:

            for artifacts in build_artifacts_helper.get_build_artifacts('ruby-src'):

                artifacts.update(self._tool_conf)
                assessment_report = osp.join(results_root_dir,
                                             assessment_report_template.format(artifacts['id']))

                if 'report-on-stdout' in artifacts \
                   and artifacts['report-on-stdout'] == 'true':
                    outfile = assessment_report
                else:
                    artifacts['assessment-report'] = assessment_report
                    outfile = osp.join(results_root_dir,
                                       'swa_tool_stdout{0}.out'.format(artifacts['id']))

                errfile = osp.join(results_root_dir,
                                   'swa_tool_stderr{0}.out'.format(artifacts['id']))

                skip_assess = 'include' not in artifacts or len(artifacts['include']) == 0

                if not skip_assess:
                    assess_cmd = gencmd.gencmd(osp.join(self.input_root_dir,
                                                        artifacts['tool-invoke']),
                                               artifacts)

                    logging.info('ASSESSMENT CMD: %s', assess_cmd)

                    start_time = utillib.posix_epoch()
                    exit_code, environ = utillib.run_cmd(assess_cmd,
                                                         outfile=outfile,
                                                         errfile=errfile,
                                                         cwd=results_root_dir,
                                                         env=self._get_env())
                    end_time = utillib.posix_epoch()

                    logging.info('ASSESSMENT WORKING DIR: %s', results_root_dir)
                    logging.info('ASSESSMENT EXIT CODE: %d', exit_code)
                    logging.info('ASSESSMENT ENVIRONMENT: %s', environ)

                    if self._validate_exit_code(exit_code):
                        passed += 1
                        execution_successful = True
                    else:
                        failed += 1
                        err_msgs += self._read_err_msg(exit_code, outfile, errfile)
                        execution_successful = False

                    #write assessment summary file
                    #return pass, fail, assessment_summary
                    assessment_summary.add_report(artifacts['id'],
                                                  assess_cmd,
                                                  exit_code,
                                                  execution_successful,
                                                  environ,
                                                  results_root_dir,
                                                  assessment_report,
                                                  assessment_report,
                                                  errfile,
                                                  start_time,
                                                  end_time)
                            
                else:
                    logging.info('ASSESSMENT SKIP (NO SOURCE FILES FOUND)')
                    
            return (passed, failed, err_msgs, assessment_summary_file)


class Dawnscanner(RubyTool):

    def __init__(self, input_root_dir, output_root_dir, tool_root_dir):
        RubyTool.__init__(self, input_root_dir, output_root_dir, tool_root_dir)

    def _get_env(self):
        gem_user_dir = subprocess.getoutput("ruby -r rubygems -e 'puts Gem.user_dir'")
        new_env = dict(os.environ)
        new_env['PATH'] = '%s/bin:%s' % (gem_user_dir, new_env.get('PATH', ''))
        new_env['GEM_PATH'] = '%s' % (gem_user_dir)
        return new_env
    

class Reek(RubyTool):

    def __init__(self, input_root_dir, output_root_dir, tool_root_dir):
        RubyTool.__init__(self, input_root_dir, output_root_dir, tool_root_dir)

    def _install(self, tool_root_dir, output_root_dir):

        with LogTaskStatus('tool-install') as status_dot_out:

            if 'tool-install-cmd' not in self._tool_conf:
                self._tool_conf['executable'] = osp.normpath(osp.join(osp.join(tool_root_dir,
                                                                               self._tool_conf['tool-dir']),
                                                                      self._tool_conf['executable']))
                status_dot_out.skip_task()
            else:
                
                install_cmd = self._tool_conf['tool-install-cmd']
                logging.info('TOOL INSTALL COMMAND: %s', install_cmd)
                exit_code, environ = utillib.run_cmd(install_cmd, 
                                                     cwd=osp.join(tool_root_dir, 
                                                                  self._tool_conf['tool-dir']),
                                                     env=self._get_env())
                logging.info('TOOL INSTALL ENVIRON: %s', environ)

                if exit_code != 0:

                    regex = re.compile(r'ruby-(?P<major>\d)[.](?P<minor>\d).(?P<patch>\d)-.+')
                    match = regex.match(os.getenv('RUBY_VERSION'))
                    version = int(''.join(match.groups()))

                    if version < 210:
                        LogTaskStatus.log_task('tool-package-compatibility',
                                               exit_code,
                                               'ruby version',
                                               'reek requires Ruby version >= 2.1.0')
                                               
                    raise ToolInstallFailedError("Install Tool Failed, "
                                                 "Command '{0}' return {1}".format(install_cmd, exit_code))
    

def assess(input_root_dir, output_root_dir, tool_root_dir,
           results_root_dir, build_summary_file):

    tool_conf_file = osp.join(input_root_dir, SwaTool.TOOL_DOT_CONF)
    tool_conf = confreader.read_conf_into_dict(tool_conf_file)

    if tool_conf['tool-type'] == 'ruby-lint':
        swatool = RubyLint(input_root_dir, output_root_dir, tool_root_dir)
    elif tool_conf['tool-type'] == 'dawnscanner':
        swatool = Dawnscanner(input_root_dir, output_root_dir, tool_root_dir)
    elif tool_conf['tool-type'] == 'reek':
        swatool = Reek(input_root_dir, output_root_dir, tool_root_dir)
    else:
        swatool = RubyTool(input_root_dir, output_root_dir, tool_root_dir)

    try:
        with LogTaskStatus('assess') as status_dot_out:

            (passed,
             failed,
             error_msgs,
             assessment_summary_file) = swatool.assess(build_summary_file, results_root_dir)

            if passed == 0 and failed == 0:
                exit_code = 0
                status_dot_out.skip_task('no files')
            else:
                exit_code = 1 if(failed) else 0
                if failed and error_msgs:
                    LogTaskStatus.log_task('tool-package-compatibility',
                                           exit_code,
                                           'known tool bug',
                                           error_msgs)

                status_dot_out.update_task_status(exit_code,
                                                  'pass: {0}, fail: {1}'.format(passed, failed))
    except (BuildArtifactsError,
            BuildSummaryError) as err:
        logging.exception(err)
        exit_code = 1
        assessment_summary_file = None

    finally:
        results_conf = dict()
        results_conf['exit-code'] = str(exit_code)

        if assessment_summary_file and osp.isfile(assessment_summary_file):
            results_conf['assessment-summary-file'] = osp.basename(assessment_summary_file)

            with LogTaskStatus('results-archive'):
                results_archive = shutil.make_archive(osp.join(output_root_dir, 'results'),
                                                      'gztar',
                                                      osp.dirname(results_root_dir),
                                                      osp.basename(results_root_dir))

                results_conf['results-archive'] = osp.basename(results_archive)
                results_conf['results-dir'] = osp.basename(results_root_dir)

                utillib.write_to_file(osp.join(output_root_dir, 'results.conf'),
                                      results_conf)

    return (exit_code, assessment_summary_file)

