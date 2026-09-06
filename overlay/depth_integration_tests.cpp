// Test real production DepthInferencer with a checked, tiny ONNX graph.
// This is implementation integration, NOT model-quality/hardware acceptance.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <d3d11.h>
#include <wrl/client.h>
#include <algorithm>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>
#include "depth_infer.h"
#include "depth_test_model.h"
using Microsoft::WRL::ComPtr;

static void Require(HRESULT hr, const char* operation) {
    if (FAILED(hr)) throw std::runtime_error(std::string(operation)+" failed: "+std::to_string(hr));
}
int main() {
    const auto path = std::filesystem::temp_directory_path()
        / ("glassless-depth-test-"+std::to_string(GetCurrentProcessId())+".onnx");
    try {
        {
            std::ofstream model(path, std::ios::binary);
            model.write(reinterpret_cast<const char*>(kDepthTestModel), sizeof(kDepthTestModel));
            if (!model) throw std::runtime_error("cannot write ONNX fixture");
        }
        ComPtr<ID3D11Device> device;
        ComPtr<ID3D11DeviceContext> context;
        const D3D_FEATURE_LEVEL feature = D3D_FEATURE_LEVEL_11_0;
        Require(D3D11CreateDevice(nullptr,D3D_DRIVER_TYPE_WARP,nullptr,D3D11_CREATE_DEVICE_BGRA_SUPPORT,
            &feature,1,D3D11_SDK_VERSION,&device,nullptr,&context),"D3D11 WARP");
        constexpr UINT W=320,H=180;
        std::vector<uint32_t> pixels(W*H);
        for(UINT y=0;y<H;++y) for(UINT x=0;x<W;++x) {
            const uint32_t v=20+(x*210/W);
            pixels[y*W+x]=0xff000000u|(v<<16)|(v<<8)|v;
        }
        D3D11_TEXTURE2D_DESC desc={};
        desc.Width=W; desc.Height=H;
        desc.MipLevels=desc.ArraySize=desc.SampleDesc.Count=1;
        desc.Format=DXGI_FORMAT_B8G8R8A8_UNORM;
        desc.BindFlags=D3D11_BIND_SHADER_RESOURCE;
        D3D11_SUBRESOURCE_DATA input={pixels.data(),W*4,0};
        ComPtr<ID3D11Texture2D> frame;
        Require(device->CreateTexture2D(&desc,&input,&frame),"capture fixture");
        {
            DepthInferencer infer;
            if(!infer.init(device.Get(),context.Get(),path.wstring(),W,H))
                throw std::runtime_error(infer.last_error());
            for(uint32_t mode : {1u,0u,2u,3u}) {
                infer.set_performance_mode(mode);
                const uint64_t baseline=infer.depth_updates_published();
                const uint64_t started=GetTickCount64();
                while(infer.depth_updates_published()<baseline+2) {
                    if(!infer.run(frame.Get())) throw std::runtime_error(infer.last_error());
                    if(GetTickCount64()-started>10000)
                        throw std::runtime_error("no useful depth publication in mode "+std::to_string(mode));
                    Sleep(2);
                }
                if(infer.depth_age_ms()>750 || !infer.depth_srv())
                    throw std::runtime_error("published depth violates freshness/resource contract");
                std::cout<<"mode="<<mode<<" accepted="<<infer.depth_updates_published()
                    <<" source_age_ms="<<infer.depth_age_ms()<<" gpu_io="<<infer.gpu_io_active()<<'\n';
            }
            ComPtr<ID3D11Resource> resource;
            infer.depth_srv()->GetResource(&resource);
            ComPtr<ID3D11Texture2D> depth;
            Require(resource.As(&depth),"depth texture");
            depth->GetDesc(&desc);
            desc.Usage=D3D11_USAGE_STAGING;
            desc.BindFlags=0;
            desc.CPUAccessFlags=D3D11_CPU_ACCESS_READ;
            ComPtr<ID3D11Texture2D> readback;
            Require(device->CreateTexture2D(&desc,nullptr,&readback),"depth readback");
            context->CopyResource(readback.Get(),depth.Get());
            D3D11_MAPPED_SUBRESOURCE mapped={};
            Require(context->Map(readback.Get(),0,D3D11_MAP_READ,0,&mapped),"depth map");
            uint16_t lo=0xffff,hi=0;
            bool finite=true;
            for(UINT y=0;y<desc.Height;++y) {
                const auto* row=reinterpret_cast<const uint16_t*>(static_cast<const char*>(mapped.pData)+y*mapped.RowPitch);
                for(UINT x=0;x<desc.Width;++x) {
                    finite=finite && (row[x]&0x7c00u)!=0x7c00u;
                    lo=std::min(lo,row[x]); hi=std::max(hi,row[x]);
                }
            }
            context->Unmap(readback.Get(),0);
            if(!finite || lo==hi) throw std::runtime_error("inference output is nonfinite or flat");
        }
        // Shutdown while the worker may be lazily constructing another profile.
        for(int iteration=0;iteration<8;++iteration) {
            DepthInferencer infer;
            if(!infer.init(device.Get(),context.Get(),path.wstring(),W,H))
                throw std::runtime_error(infer.last_error());
            infer.set_performance_mode(iteration%2 ? 0u : 2u);
            for(int tick=0;tick<5;++tick) {
                if(!infer.run(frame.Get())) throw std::runtime_error(infer.last_error());
                Sleep(1);
            }
        }
        std::filesystem::remove(path);
        std::cout<<"PASS: production DirectML pipeline, profile changes and shutdown stress\n";
        return 0;
    } catch(const std::exception& error) {
        std::error_code ignored;
        std::filesystem::remove(path,ignored);
        std::cerr<<error.what()<<'\n';
        return 1;
    }
}
