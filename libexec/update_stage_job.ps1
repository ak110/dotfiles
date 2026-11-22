Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if (-not ('UpdateStageJob' -as [type])) {
Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.IO;
using System.IO.Pipes;
using System.Runtime.InteropServices;
using System.ComponentModel;
using System.Threading;
using System.Threading.Tasks;
public sealed class UpdateStageJob : IDisposable {
    [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]
    struct Startup { public int size; public string reserved, desktop, title; public int x,y,w,h,cx,cy,fill,flags; public short show,cb; public IntPtr data,input,output,error; }
    [StructLayout(LayoutKind.Sequential)]
    struct ProcessInfo { public IntPtr process,thread; public int pid,tid; }
    [StructLayout(LayoutKind.Sequential)]
    struct Accounting { public long a,b,c,d; public uint faults,total,active,terminated; }
    [StructLayout(LayoutKind.Sequential)]
    struct BasicLimits { public long processTime,jobTime; public uint flags; public UIntPtr min,max; public uint active; public UIntPtr affinity; public uint priority,scheduling; }
    [StructLayout(LayoutKind.Sequential)]
    struct Limits { public BasicLimits basic; public ulong a,b,c,d,e,f; public UIntPtr processMemory,jobMemory,peakProcess,peakJob; }
    [DllImport("kernel32", SetLastError=true)] static extern bool SetInformationJobObject(IntPtr job,int kind,ref Limits limits,int size);
    [DllImport("kernel32", SetLastError=true)] static extern IntPtr CreateJobObject(IntPtr attributes,string name);
    [DllImport("kernel32", SetLastError=true)] static extern bool AssignProcessToJobObject(IntPtr job,IntPtr process);
    [DllImport("kernel32", SetLastError=true,CharSet=CharSet.Unicode)] static extern bool CreateProcess(string app,StringBuilder cmd,IntPtr pa,IntPtr ta,bool inherit,uint flags,IntPtr env,string cwd,ref Startup startup,out ProcessInfo info);
    [DllImport("kernel32", SetLastError=true)] static extern uint ResumeThread(IntPtr thread);
    [DllImport("kernel32", SetLastError=true)] static extern bool TerminateJobObject(IntPtr job,uint code);
    [DllImport("kernel32", SetLastError=true)] static extern bool TerminateProcess(IntPtr process,uint code);
    [DllImport("kernel32", SetLastError=true)] static extern bool QueryInformationJobObject(IntPtr job,int kind,out Accounting info,int size,IntPtr length);
    [DllImport("kernel32", SetLastError=true)] static extern uint WaitForSingleObject(IntPtr handle,uint milliseconds);
    [DllImport("kernel32", SetLastError=true)] static extern bool GetExitCodeProcess(IntPtr process,out uint code);
    [DllImport("kernel32")] static extern IntPtr GetStdHandle(int kind);
    [DllImport("kernel32")] static extern bool CloseHandle(IntPtr handle);
    IntPtr job,process; AnonymousPipeServerStream pipe, diagnosticPipe; Task<string> output, diagnostic; bool disposed;
    static Exception Error() { return new Win32Exception(Marshal.GetLastWin32Error()); }
    public UpdateStageJob(string program,string arguments,bool probe,TextWriter diagnostics) {
        job=CreateJobObject(IntPtr.Zero,null); if(job==IntPtr.Zero) throw Error();
        ProcessInfo info=new ProcessInfo();
        try {
            Limits limits=new Limits(); limits.basic.flags=0x2000|0x800;
            if(!SetInformationJobObject(job,9,ref limits,Marshal.SizeOf(typeof(Limits)))) throw Error();
            Startup startup=new Startup(); startup.size=Marshal.SizeOf(startup); startup.flags=0x100;
            startup.input=GetStdHandle(-10); startup.output=GetStdHandle(-11); startup.error=GetStdHandle(-12);
            pipe=new AnonymousPipeServerStream(PipeDirection.In,HandleInheritability.Inheritable);
            startup.output=pipe.ClientSafePipeHandle.DangerousGetHandle();
            if(probe) {
                diagnosticPipe=new AnonymousPipeServerStream(PipeDirection.In,HandleInheritability.Inheritable);
                startup.error=diagnosticPipe.ClientSafePipeHandle.DangerousGetHandle();
            } else startup.error=startup.output;
            if(!CreateProcess(program,new StringBuilder("\""+program+"\" "+arguments),IntPtr.Zero,IntPtr.Zero,true,4|0x08000000,IntPtr.Zero,null,ref startup,out info)) throw Error();
            process=info.process;
            if(!AssignProcessToJobObject(job,process)) throw Error();
            if(ResumeThread(info.thread)==0xffffffff) throw Error();
            output=StartReader(pipe,probe ? null : diagnostics);
            if(probe) diagnostic=StartReader(diagnosticPipe,diagnostics);
        } catch (Exception failure) {
            try {
                if(process!=IntPtr.Zero) {
                    if(!TerminateProcess(process,1) && WaitForSingleObject(process,0)!=0) throw Error();
                    if(WaitForSingleObject(process,5000)!=0) throw new TimeoutException("停止中プロセスが残っています");
                }
                Dispose();
            } catch(Exception cleanup) { throw new TimeoutException("開始失敗後の所有を確認できません",new AggregateException(failure,cleanup)); }
            throw;
        }
        finally { if(info.thread!=IntPtr.Zero) CloseHandle(info.thread); }
    }
    public bool Wait(int milliseconds) { uint result=WaitForSingleObject(process,(uint)milliseconds); if(result==0xffffffff) throw Error(); return result==0; }
    public int ExitCode { get { uint code; if(!GetExitCodeProcess(process,out code)) throw Error(); return (int)code; } }
    public void Stop() {
        if(!TerminateJobObject(job,1)) throw Error();
        var clock=System.Diagnostics.Stopwatch.StartNew();
        while(true) { Accounting info; if(!QueryInformationJobObject(job,1,out info,Marshal.SizeOf(typeof(Accounting)),IntPtr.Zero)) throw Error(); if(info.active==0) return; if(clock.ElapsedMilliseconds>=5000) throw new TimeoutException("Jobの所属プロセスが残っています"); Thread.Sleep(10); }
    }
    static Task<string> StartReader(AnonymousPipeServerStream stream, TextWriter diagnostics) {
        stream.DisposeLocalCopyOfClientHandle();
        return Task.Run(()=>{
            using (var reader=new StreamReader(stream,Encoding.UTF8)) {
                if (diagnostics==null) return reader.ReadToEnd();
                string line;
                while ((line=reader.ReadLine())!=null) diagnostics.WriteLine(line);
                return "";
            }
        });
    }
    static string Read(Task<string> reader) {
        if(reader==null) return "";
        if(!reader.Wait(5000)) throw new TimeoutException("出力readerが終了しません");
        return reader.Result;
    }
    public string ReadOutput() { return Read(output); }
    public string ReadDiagnostic() { return Read(diagnostic); }
    public void Dispose() {
        if(disposed) return; disposed=true;
        try { if(job!=IntPtr.Zero) Stop(); }
        finally {
            try {
                if(pipe!=null) pipe.Dispose();
                if(diagnosticPipe!=null) diagnosticPipe.Dispose();
                Read(output); Read(diagnostic);
            } finally {
                if(process!=IntPtr.Zero) CloseHandle(process);
                if(job!=IntPtr.Zero) CloseHandle(job);
            }
        }
    }
}
'@
}
function Start-OwnedUpdateStageProcess {
    [Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSUseShouldProcessForStateChangingFunctions', '', Justification = '更新段の内部起動。実行の採否は呼び出し元が判定し、生成直後のJob所有を保証する。')]
    param([string]$Program, [string[]]$Arguments, [bool]$Probe, [IO.TextWriter]$Diagnostics)
    # CommandLineToArgvW互換の引用で、末尾のバックスラッシュも引数内へ保つ。
    $quoted = @($Arguments | ForEach-Object { '"' + [regex]::Replace([regex]::Replace($_, '(\\*)"', '$1$1\"'), '(\\+)$', '$1$1') + '"' }) -join ' '
    $executable = (Get-Command -Name $Program -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
    return [UpdateStageJob]::new($executable, $quoted, $Probe, $Diagnostics)
}
