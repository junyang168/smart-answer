// Read-only endurance supervisor. The installed app is started/stopped through
// its normal UI; this helper never injects input or accesses app private IPC.
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const { execFileSync } = require('node:child_process');
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function run(target, seconds, evidence, executable = '/Applications/Sunday Presenter.app/Contents/MacOS/Sunday Presenter') {
  const prefix = path.dirname(path.dirname(path.dirname(executable))) + path.sep;
  if (!path.isAbsolute(target) || !Number.isFinite(seconds) || seconds < 1) throw Error('target, seconds, evidence required');
  await fs.mkdir(evidence, { recursive: true });
  const folder = path.dirname(target), name = path.basename(target);
  const partials = (await fs.readdir(folder)).filter(n => n.startsWith(name + '.') && n.endsWith('.partial.mp4'));
  if (partials.length !== 1) throw Error('Expected exactly one active partial file');
  const partial = path.join(folder, partials[0]);
  const initial = await fs.stat(partial), startedAt = initial.birthtimeMs;
  const stopDueAt = startedAt + (seconds + 10) * 1000;
  const readProcesses = () => execFileSync('ps', ['-axo', 'pid=,rss=,%cpu=,comm='], { encoding: 'utf8' }).split('\n').flatMap(line => {
    const match = line.match(/^\s*(\d+)\s+(\d+)\s+([\d.]+)\s+(.+)$/);
    return match && match[4].startsWith(prefix) ? [{ pid: +match[1], rssKiB: +match[2], cpu: +match[3], name: path.basename(match[4]) }] : [];
  });
  let lastBytes = initial.size, lastGrowth = Date.now(), nextLog = 0, samples = 0;
  const journal = path.join(evidence, 'progress.jsonl');
  console.log(JSON.stringify({ event:'started',target,partial,seconds,startedAt:new Date(startedAt).toISOString(),stopDueAt:new Date(stopDueAt).toISOString() }));
  while (true) {
    const now = Date.now(), file = await fs.stat(partial).catch(() => null);
    const completed = await fs.stat(target).catch(() => null);
    const processes = readProcesses(), main = processes.find(p => p.name === path.basename(executable));
    if (file && file.size > lastBytes) { lastBytes = file.size; lastGrowth = now; }
    const free = await fs.statfs(folder);
    const state = { now:new Date(now).toISOString(),startedAt:new Date(startedAt).toISOString(),stopDueAt:new Date(stopDueAt).toISOString(),
      elapsedSeconds:(now-startedAt)/1000,bytes:file?.size || completed?.size || 0,secondsWithoutGrowth:(now-lastGrowth)/1000,
      status:completed?'finalized':file?'writing':'missing',stopDue:now>=stopDueAt,mainPid:main?.pid,
      processRSSMiB:processes.reduce((n,p)=>n+p.rssKiB,0)/1024,cpuPercent:processes.reduce((n,p)=>n+p.cpu,0),
      freeDiskGiB:free.bavail*free.bsize/2**30,systemFreeMiB:os.freemem()/2**20,processes };
    const failure = !main ? 'Presenter process exited' : completed && now < stopDueAt ? 'Recording finalized before endurance deadline' : !file && !completed ? 'Recording file disappeared' : now-lastGrowth>15000 && !completed ? 'Recording stopped growing for 15 seconds' : '';
    if (failure) { state.failure=failure; await fs.writeFile(path.join(evidence,'latest.json'),JSON.stringify(state,null,2)); await fs.appendFile(journal,JSON.stringify(state)+'\n'); throw Error(failure); }
    await fs.writeFile(path.join(evidence,'latest.json'),JSON.stringify(state,null,2));
    if (now>=nextLog || state.stopDue) {
      await fs.appendFile(journal,JSON.stringify(state)+'\n'); samples++;
      console.log(JSON.stringify({ event:state.stopDue?'STOP_DUE':'progress',minutes:Math.floor(state.elapsedSeconds/60),MiB:Math.round(state.bytes/2**20),RSSMiB:Math.round(state.processRSSMiB),cpu:state.cpuPercent,samples }));
      nextLog=now+60000;
    }
    if (state.stopDue) return state;
    await sleep(5000);
  }
}
if (require.main===module) run(process.argv[2],Number(process.argv[3]),process.argv[4],process.argv[5]).catch(error=>{console.error(error);process.exitCode=1;});
module.exports={run};
