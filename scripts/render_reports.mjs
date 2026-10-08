#!/usr/bin/env node
// Optional editable PPTX exporter. PDF export is independently available in render_reports.py.
import fs from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { createRequire } from 'node:module';

const argv = process.argv.slice(2);
if (argv.includes('--help') || argv.includes('-h')) {
  console.log('Usage: node render_reports.mjs --model report-model.json --output-dir reports [--work-dir work/report-build] [--skill-dir path --python path]\nOptional PPTX exporter requires @oai/artifact-tool. For portable PDF output use render_reports.py with reportlab.');
  process.exit(0);
}
const opt = name => {const i = argv.indexOf(name); return i < 0 ? null : argv[i + 1];};
const modelPath = opt('--model');
const outputDir = opt('--output-dir');
if (!modelPath || !outputDir) throw new Error('Usage: node render_reports.mjs --model report-model.json --output-dir outputs [--skill-dir path --python path --work-dir path]');
const work = path.resolve(opt('--work-dir') || 'work/report-build');
const moduleRoot = process.env.ARTIFACT_NODE_MODULES;
if (moduleRoot && !process.env.RUNTIME_NODE_MODULES) process.env.RUNTIME_NODE_MODULES = moduleRoot;
const req = createRequire(import.meta.url);
const modulePath = moduleRoot ? path.join(moduleRoot, '@oai/artifact-tool/dist/artifact_tool.mjs') : req.resolve('@oai/artifact-tool');
let art;
try { art = await import(pathToFileURL(modulePath).href); }
catch (error) {
  if (!moduleRoot) throw error;
  const packageFile = JSON.parse(await fs.readFile(path.join(moduleRoot, '@oai/artifact-tool/package.json'), 'utf8'));
  const main = packageFile.exports?.['.']?.import || packageFile.module || packageFile.main;
  art = await import(pathToFileURL(path.resolve(moduleRoot, '@oai/artifact-tool', main)).href);
}
const {Presentation, PresentationFile} = art;
const model = JSON.parse(await fs.readFile(modelPath, 'utf8'));
const presentation = Presentation.create({slideSize:{width:model.width,height:model.height}});
await fs.mkdir(work, {recursive:true});
await fs.mkdir(outputDir, {recursive:true});
const nativeTables = [];
for (const [i, item] of model.slides.entries()) {
  const sl = presentation.slides.add();
  sl.background.fill = item.background;
  // Client decks must not inherit arbitrary notes from a cached/edited model.
  // Only public source metadata is allowed, even if item.notes contains data.
  const publicNotes = [
    typeof model.source_label === 'string' ? model.source_label : '资料来源见正文',
    typeof model.source_date === 'string' ? `资料采集日期：${model.source_date}` : '',
    typeof model.report_date === 'string' ? `报告日期：${model.report_date}` : '',
  ].filter(Boolean).join('\n');
  sl.speakerNotes.textFrame.setText(publicNotes);
  for (const el of item.elements) {
    if (el.kind === 'text') {
      const sh = sl.shapes.add({geometry:'textbox',position:{left:el.x,top:el.y,width:el.w,height:el.h},fill:'none',line:{fill:'none',width:0}});
      sh.text = el.text;
      sh.text.style = {typeface:model.font,fontSize:el.size,bold:el.bold,color:el.color,autoFit:'none',wrap:'square',verticalAlignment:'top',insets:{left:0,right:0,top:0,bottom:0}};
    } else {
      const tab = sl.tables.add({rows:el.values.length,columns:el.widths.length,left:el.x,top:el.y,width:el.w,height:el.h,columnWidths:el.widths,values:el.values});
      nativeTables.push(i+1);
      tab.cells.block({row:0,column:0,rowCount:el.values.length,columnCount:el.widths.length}).assign({margins:{left:10,right:10,top:2,bottom:2}});
      for (let r=0;r<el.values.length;r++) {
        tab.rows[r].height = el.row_height;
        for (let c=0;c<el.widths.length;c++) {
          const cell = tab.getCell(r,c);
          cell.fill = r===0 ? '#172E31' : (c===0 ? '#E5EBE4' : (r%2 ? '#FFFFFF' : '#ECEFE9'));
          cell.text.style = {typeface:model.font,fontSize:el.size,color:r===0?'#FFFFFF':'#172E31',bold:r===0,verticalAlignment:'middle',autoFit:'none',insets:{left:14,right:14,top:7,bottom:7}};
        }
      }
      tab.borders.assign({fill:'#F5F3ED',color:'#F5F3ED',width:1});
    }
  }
}
const stem = model.simulation ? '上海新房_客户选房方案_演示' : '上海新房_客户选房方案';
const candidate = path.join(work,'candidate.pptx');
const final = path.resolve(outputDir,stem+'.pptx');
if ([final, candidate].includes(path.resolve(modelPath))) throw new Error('Output path conflicts with input model; refusing to overwrite it.');
await (await PresentationFile.exportPptx(presentation)).save(candidate);
const skillDir=opt('--skill-dir');
if (skillDir) {
  const {finalizePresentation} = await import(pathToFileURL(path.join(skillDir,'container_tools/artifact_tool_utils.mjs')).href);
  const runId = Date.now();
  await fs.mkdir(path.join(work,'validated'),{recursive:true});
  const validatedPath = path.join(work,'validated',`validated-${runId}.pptx`);
  await finalizePresentation({workspaceDir:process.cwd(),candidatePath:candidate,finalPath:validatedPath,
    pythonExecutable:opt('--python')||'python3',
    integrityValidatorPath:path.join(skillDir,'container_tools/inspect_presentation_package_integrity.py'),
    layoutValidatorPath:path.join(skillDir,'container_tools/inspect_presentation_layout_geometry.py'),
    layoutArgs:['--expected-slide-size-emu','12192000,6858000','--validate-heading-fit',...nativeTables.flatMap(n=>['--require-native-table-slide',String(n)])],
    explicitTotalSlideCount:model.slides.length,requiredNativeTableOwnerSlides:nativeTables,
    fontPolicy:{basis:'design',families:[model.font]},verifyArtifactToolImport:true,
    receiptPath:path.join(work,`presentation-${runId}.validation.json`)});
  await fs.copyFile(validatedPath,final);
} else {
  await fs.copyFile(candidate,final);
}
const renderedPresentation = await PresentationFile.importPptx(await art.FileBlob.load(final));
for (const [i, sl] of [...renderedPresentation.slides.items].entries()) {
  const blob = await renderedPresentation.export({slide:sl,format:'png',scale:1});
  await fs.writeFile(path.join(work,`slide-${i+1}.png`),new Uint8Array(await blob.arrayBuffer()));
}
console.log(JSON.stringify({pptx:final,slides:model.slides.length,nativeTableSlides:nativeTables}));
