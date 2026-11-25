import { test as base, expect } from '@playwright/test'
import { instrumentBrowserTests } from '../../frontend/browser-diagnostics.mjs'

const test = instrumentBrowserTests(base, expect)
for (const [width,role] of [[390,'owner_finance'],[1280,'owner_manager']]) {
  test(`${role} generates and downloads own workbook at ${width}px`,async({page},testInfo)=>{
    await page.setViewportSize({width,height:840})
    const capabilities={can_export_owner_archive:true}
    const user={id:5,owner_id:2,roles:[role],capabilities}
    await page.addInitScript(user=>{
      localStorage.setItem('access','synthetic-archive-token')
      localStorage.setItem('user',JSON.stringify({type:'object',data:user}))
    },user)
    let submitted=false, polls=0
    await page.route('**/api/**',async route=>{
      const path=new URL(route.request().url()).pathname
      if(path.endsWith('/profile/')) return route.fulfill({json:{user,capabilities}})
      if(path.endsWith('/download/')) return route.fulfill({body:Buffer.from('PK-test-download'),contentType:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'})
      if(path.endsWith('/owner-archives/')){
        const job={id:7,status:polls>0?'ready':'queued',created_at:'2026-11-21T12:00:00+08:00'}
        if(route.request().method()==='POST'){
          expect(route.request().postDataJSON()).toEqual({})
          submitted=true
          return route.fulfill({status:202,json:job})
        }
        if(submitted) polls++
        return route.fulfill({json:{results:submitted?[job]:[]}})
      }
      return route.fulfill({json:{results:[]}})
    })
    await page.goto('/#/pages/home/index')
    await page.waitForLoadState('networkidle')
    await page.locator('uni-tabbar').getByText('报表',{exact:true}).click()
    await page.waitForLoadState('networkidle')
    await page.locator('.card').filter({hasText:'导出完整 Excel'}).click()
    const panel=page.locator('.archive')
    await expect(panel.getByText('全部历史',{exact:true})).toBeVisible()
    await panel.getByText('自选日期',{exact:true}).click()
    await panel.getByText('导出完整 Excel',{exact:true}).click()
    await expect(panel).toContainText('请选择有效的开始和结束日期')
    await panel.getByText('全部历史',{exact:true}).click()
    const downloaded=page.waitForEvent('download')
    await panel.getByText('导出完整 Excel',{exact:true}).click()
    expect((await downloaded).suggestedFilename()).toContain('全部历史-7.xlsx')
    await expect(panel).toContainText('可下载')
    await page.screenshot({path:testInfo.outputPath('owner-archive-'+width+'.png')})
  })
}
