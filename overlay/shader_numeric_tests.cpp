// Compile the shipped shaders and execute the production depth sampling function on WARP.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <d3d11.h>
#include <d3dcompiler.h>
#include <wrl/client.h>
#include <algorithm>
#include <array>
#include <cmath>
#include <fstream>
#include <iostream>
#include <iterator>
#include <stdexcept>
#include <string>
#include "depth_cohesion_shader.h"
using Microsoft::WRL::ComPtr;
static void Require(HRESULT hr, const char* op) {
    if (FAILED(hr)) throw std::runtime_error(std::string(op)+": "+std::to_string(hr));
}
static ComPtr<ID3DBlob> Compile(const std::string& text, const char* entry, const char* target) {
    ComPtr<ID3DBlob> code, error;
    const HRESULT hr=D3DCompile(text.data(), text.size(), "production-shader-test", nullptr,
        nullptr, entry, target, D3DCOMPILE_ENABLE_STRICTNESS, 0, &code, &error);
    if (FAILED(hr)) throw std::runtime_error(error
        ? std::string(static_cast<const char*>(error->GetBufferPointer()), error->GetBufferSize())
        : "D3DCompile failed");
    return code;
}
static std::string Extract(const std::string& source, const char* name) {
    const auto marker=source.find(std::string("static const char ")+name+"[]");
    const auto start=source.find("R\"hlsl(", marker);
    const auto end=source.find(")hlsl\"", start);
    if(marker==std::string::npos || start==std::string::npos || end==std::string::npos)
        throw std::runtime_error("production shader not found");
    return source.substr(start+7, end-start-7);
}
static float Oracle(std::array<float,5> samples) {
    const float center=samples[0];
    std::sort(samples.begin(),samples.end());
    const float t=std::clamp((samples[4]-samples[0]-0.05f)/(0.24f-0.05f),0.0f,1.0f);
    const float edge=t*t*(3.0f-2.0f*t)*0.70f;
    const float trimmed=(samples[1]+samples[2]+samples[3])/3.0f;
    return center*(1.0f-edge)+trimmed*edge;
}
int main(int argc,char** argv) {
    try {
        if(argc!=2) throw std::runtime_error("usage: shader_numeric_tests overlay.cpp");
        std::ifstream stream(argv[1],std::ios::binary);
        if(!stream) throw std::runtime_error("cannot read production overlay source");
        const std::string source((std::istreambuf_iterator<char>(stream)),{});
        const auto vs=Compile(Extract(source,"VS_SRC"),"main","vs_5_0");
        const std::string ps=std::string(G3D_DEPTH_COHESION_HLSL)+Extract(source,"PS_SRC");
        Compile(ps,"main","ps_5_0"); // The actual rendering entrypoint must compile too.
        const auto harness=Compile(ps+R"(
float4 audit_main(float4 position:SV_Position):SV_Target {
    DepthSample d=SampleDepthCohesive(float2(0.5,0.5),1.0,float2(0.2,0),float2(0,0.2));
    return float4(d.depth,d.rawDepth,d.range,d.confidence);
})","audit_main","ps_5_0");
        ComPtr<ID3D11Device> dev;
        ComPtr<ID3D11DeviceContext> ctx;
        const D3D_FEATURE_LEVEL feature=D3D_FEATURE_LEVEL_11_0;
        Require(D3D11CreateDevice(nullptr,D3D_DRIVER_TYPE_WARP,nullptr,0,&feature,1,
            D3D11_SDK_VERSION,&dev,nullptr,&ctx),"create WARP device");
        ComPtr<ID3D11VertexShader> vertex;
        ComPtr<ID3D11PixelShader> pixel;
        Require(dev->CreateVertexShader(vs->GetBufferPointer(),vs->GetBufferSize(),nullptr,&vertex),"create VS");
        Require(dev->CreatePixelShader(harness->GetBufferPointer(),harness->GetBufferSize(),nullptr,&pixel),"create PS");
        D3D11_TEXTURE2D_DESC desc={};
        desc.Width=desc.Height=desc.MipLevels=desc.ArraySize=desc.SampleDesc.Count=1;
        desc.Format=DXGI_FORMAT_R32G32B32A32_FLOAT;
        desc.BindFlags=D3D11_BIND_RENDER_TARGET;
        ComPtr<ID3D11Texture2D> output,readback;
        Require(dev->CreateTexture2D(&desc,nullptr,&output),"create output");
        ComPtr<ID3D11RenderTargetView> rtv;
        Require(dev->CreateRenderTargetView(output.Get(),nullptr,&rtv),"create RTV");
        desc.BindFlags=0; desc.Usage=D3D11_USAGE_STAGING; desc.CPUAccessFlags=D3D11_CPU_ACCESS_READ;
        Require(dev->CreateTexture2D(&desc,nullptr,&readback),"create readback");
        std::array<float,20> constants={};
        constants[9]=1; constants[13]=1; constants[14]=1;
        D3D11_BUFFER_DESC bd={}; bd.ByteWidth=sizeof(constants); bd.BindFlags=D3D11_BIND_CONSTANT_BUFFER;
        D3D11_SUBRESOURCE_DATA initial={constants.data(),0,0};
        ComPtr<ID3D11Buffer> cb;
        Require(dev->CreateBuffer(&bd,&initial,&cb),"create constants");
        D3D11_SAMPLER_DESC sd={}; sd.Filter=D3D11_FILTER_MIN_MAG_MIP_POINT;
        sd.AddressU=sd.AddressV=sd.AddressW=D3D11_TEXTURE_ADDRESS_CLAMP;
        sd.MaxLOD=D3D11_FLOAT32_MAX;
        ComPtr<ID3D11SamplerState> sampler;
        Require(dev->CreateSamplerState(&sd,&sampler),"create sampler");
        D3D11_VIEWPORT viewport={0,0,1,1,0,1};
        ctx->RSSetViewports(1,&viewport);
        D3D11_RASTERIZER_DESC rd={}; rd.FillMode=D3D11_FILL_SOLID; rd.CullMode=D3D11_CULL_NONE;
        ComPtr<ID3D11RasterizerState> rasterizer;
        Require(dev->CreateRasterizerState(&rd,&rasterizer),"create rasterizer");
        ctx->RSSetState(rasterizer.Get());
        ctx->OMSetRenderTargets(1,rtv.GetAddressOf(),nullptr);
        ctx->VSSetShader(vertex.Get(),nullptr,0); ctx->PSSetShader(pixel.Get(),nullptr,0);
        ctx->PSSetConstantBuffers(0,1,cb.GetAddressOf());
        ctx->PSSetSamplers(1,1,sampler.GetAddressOf());
        ctx->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLESTRIP);
        const std::array<std::array<float,5>,6> cases={{{.5f,.5f,.5f,.5f,.5f},
            {.5f,.1f,.5f,.5f,.9f},{0,0,0,0,0},{1,1,1,1,1},
            {.1f,.2f,.4f,.6f,.8f},{.5f,.5f,.5f,.5f,1}}};
        for(const auto& sample:cases) {
            std::array<float,25> depth; depth.fill(sample[0]);
            depth[12]=sample[0];depth[11]=sample[1];depth[13]=sample[2];depth[7]=sample[3];depth[17]=sample[4];
            desc={};desc.Width=desc.Height=5;desc.MipLevels=desc.ArraySize=desc.SampleDesc.Count=1;
            desc.Format=DXGI_FORMAT_R32_FLOAT;desc.BindFlags=D3D11_BIND_SHADER_RESOURCE;
            D3D11_SUBRESOURCE_DATA input={depth.data(),5*sizeof(float),0};
            ComPtr<ID3D11Texture2D> tex; ComPtr<ID3D11ShaderResourceView> srv;
            Require(dev->CreateTexture2D(&desc,&input,&tex),"create depth fixture");
            Require(dev->CreateShaderResourceView(tex.Get(),nullptr,&srv),"create depth SRV");
            ID3D11ShaderResourceView* views[]={srv.Get(),srv.Get()};
            ctx->PSSetShaderResources(1,2,views);ctx->Draw(4,0);
            ctx->CopyResource(readback.Get(),output.Get());
            D3D11_MAPPED_SUBRESOURCE mapped={};
            Require(ctx->Map(readback.Get(),0,D3D11_MAP_READ,0,&mapped),"map output");
            const float actual=static_cast<const float*>(mapped.pData)[0];
            ctx->Unmap(readback.Get(),0);
            const float expected=Oracle(sample);
            if(!std::isfinite(actual)||std::fabs(actual-expected)>2e-5f)
                throw std::runtime_error("production depth mismatch: actual="+std::to_string(actual)+" expected="+std::to_string(expected));
            std::cout<<"depth="<<actual<<" oracle="<<expected<<'\n';
        }
        std::cout<<"PASS: production HLSL compile and six WARP numeric cases\n";
        return 0;
    } catch(const std::exception& e) { std::cerr<<e.what()<<'\n';return 1; }
}
