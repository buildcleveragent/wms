<template>
	<view>
		<button @click="searchBle">搜索蓝牙</button>
		 状态：<text> {{textValue}}</text>
		<view style="margin-top: 2upx;" :key="index" v-for="(item,index) in devices">
			<button style="  color: #0081FF;" @click="onConn(item)">{{item.name}}</button>
		</view>
		<button style="margin-top: 100upx;" @click="senBleLabel()">标签打印</button>
		<textarea @blur="piaojuText" auto-height placeholder-style="color:#F76260" placeholder="请输入票据信息" v-model="piaojuText" />
		<button style="margin-top: 100upx;" @click="senBleLabel3()">票据打印</button>
	</view>


</template>

<script>
	
	var main = plus.android.runtimeMainActivity();
	// 加载插件 获取 module nani
	var blePlatform = uni.requireNativePlugin("TH-PlatformBLE")//新增方法  引用插件
	
	export default {
		data() {
			return {
				devices: [],
				currDev: null,
				connId: '',
				piaojuText:'',
				 textValue: '',
				 // 标签模板
				 templateType: "40*30",
				 countPrint:0,
			}
		},
		onLoad: function() {
		
		},
		onUnload:function(){
			this.destroyed();
		},

	onShow: function(){
			var that = this
			blePlatform.connState((ret) => {//新增方法 (蓝牙连接的状态回调)
					console.log("connState() "+ret)
					this.textValue =""+ret;
					if(ret == 0){
						//连接成功 关闭搜索
						console.log("connect succes.")
					}else if(ret == 10){
						//搜索蓝牙设备结束
						 // 隐藏加载框
						uni.hideLoading();
					}
				});
			
	
	},
		methods: {
			destroyed: function() {
				console.log("destroyed----------")
				blePlatform.bluetoothDisConn();//断开连接
			},
			searchBle() {
				var that = this
				that.devices.length = 0;
				console.log("initBule")
				uni.openBluetoothAdapter({
					success(res) {
						console.log("打开 蓝牙模块")
						uni.getBluetoothAdapterState({
							success: function(res) {
									console.log("开始搜寻附近的蓝牙外围设备")
									uni.startBluetoothDevicesDiscovery({
										success(res) {
											console.log("scan device : "+JSON.stringify(res))
											// 显示加载框
											uni.showLoading({
											    title: "搜索设备中..."
											});
											
											blePlatform.startSearchDevice((result) => {
											console.log("startSearchDevice : "+JSON.stringify(result))
											let nameUI = result.name+"(" +result.deviceId+")"
											let deviceId = result.deviceId
											
											that.devices.push({
												name: nameUI,
												deviceId: deviceId,
												services: []
												})
														
														
												});
											
											
										}
									})

								// } else {
								// 	console.log('本机蓝牙不可用')
								// }
							},
						})
					}
				})
			},
			 fetchFriendsList() {
			      // 模拟异步获取朋友列表
			      return new Promise((resolve, reject) => {
			        
			      });
			    },
			onConn(item) {
				blePlatform.stopDiscoveryDevice();//先停止搜索蓝牙设备
				
				var that = this
				let deviceId = item.deviceId
				blePlatform.bluetoothConn(deviceId);//新增方法 开始连接蓝牙，下面的原生连接已注释
				
				console.log("连接蓝牙-[" + item.name + "]-")
			},

			senBlData(deviceId, serviceId, characteristicId,uint8Array) {
				console.log('************deviceId = [' + deviceId + ']  serviceId = [' + serviceId + '] characteristics=[' +characteristicId+ "]")
				var uint8Buf = Array.from(uint8Array);
				function split_array(datas,size){
					var result = {};
					var j = 0
					for (var i = 0; i < datas.length; i += size) {
						result[j] = datas.slice(i, i + size)
						j++
					}
					console.log("split_array() result:"+result)
					return result
				}
				var sendloop = split_array(uint8Buf, 20);
				// console.log(sendloop.length)
				function realWriteData(sendloop, i) {
					var data = sendloop[i]
					if(typeof(data) == "undefined"){
						return
					}
					console.log("第【" + i + "】次写数据"+data)
					var buffer = new ArrayBuffer(data.length)
					var dataView = new DataView(buffer)
					for (var j = 0; j < data.length; j++) {
						dataView.setUint8(j, data[j]);
					}
					uni.writeBLECharacteristicValue({
						deviceId,
						serviceId,
						characteristicId,
						value: buffer,
						success(res) {
							realWriteData(sendloop, i + 1);
						}
					})
				}
               var i = 0;
				realWriteData(sendloop, i);
			},
			
			senBleLabel() {
				var state = blePlatform.bluetoothGetState()//0 连接，1：断开
				console.log(" 连接状态 " + state )
				this.countPrint =0;
				this.senBleLabelFor();
				},
			senBleLabelFor() {
				let that = this
				this.countPrint ++
				
				let data = {
								            //model：数据
								            code: "QR1234567890ABCDEF",
											materialName: "贴片电阻/100R/%0402",
											materialCode: "123456789",
											supplierName: "容奥",
											mfgTime: "2023-06-06",
											qty: "1205",
											po: "4500014628",
											supplierBatch: "20230815-S001133",
											
								        }
							//标签模式
							console.log("打印1", data)
							
								let canvasWidth = 40
								let canvasHeight = 50
								let {
									toLine,
									splitStr,
									notNullData
								} = that
								blePlatform.clear()//新增方法 清除数据
								blePlatform.setSize(40, 33)
								blePlatform.setGap(0)
								blePlatform.setCls()
								blePlatform.setBackFeed(0)
								let col = 0
								let font = "TSS16.BF2"
								blePlatform.setText(col, toLine(1), font, 1, 1, "物料编码\"\:测试")
								blePlatform.setQR(toLine(11), toLine(1), "L", 3, "A", notNullData(data.code))
								let codeStr = notNullData(data.code)
								let codeList = splitStr(codeStr, 11)
								for (let codeListIndex = 0; codeListIndex < codeList.length; codeListIndex++) {
									blePlatform.setText(toLine(11), toLine((5 + codeListIndex)), font, 1, 1, codeList[codeListIndex])
								}
								blePlatform.setText(col, toLine(2), font, 2, 2, notNullData(data.materialCode))
								blePlatform.setText(col, toLine(4), font, 1, 1, "物料名称:")
								let materialName = notNullData(data.materialName)
								let materialNameList = splitStr(materialName, 20)
								let i = 0
								for (; i < materialNameList.length; i++) {
									blePlatform.setText(col, toLine(5 + i), font, 1, 1, materialNameList[i])
								}
								blePlatform.setText(col, toLine(5 + i), font, 1, 1,  "供应商简称:" + notNullData(data.supplierName))
								blePlatform.setText(col, toLine(6 + i), font, 1, 1,  "供应商生产白期:" + notNullData(data.mfgTime))
								blePlatform.setText(toLine(11), toLine(6 + i), font, 1, 1, "数量:")
								blePlatform.setText(toLine(13), toLine(6 + i), font, 2, 2, notNullData(data.qty))
								blePlatform.setText(col, toLine(7 + i), font, 1, 1, "采购单号:" + notNullData(data.po))
								blePlatform.setText(col, toLine(8 + i), font, 1, 1, "供应商生产批次:" + notNullData(data.supplierBatch))
								console.log("打印2", blePlatform)
								blePlatform.setPagePrint()
								let ret = blePlatform.startPrint();//新增方法 开始打印
								console.log("打印2", "startPrint()"+ret)
								
								
								if(this.countPrint <5){//打印10次
								setTimeout(function() {
								 that.senBleLabelFor()
								}, 500); // 这里的 1000 表示延时的时间，单位是毫秒
								}
								
								// uni.canvasGetImageData({
								// 	canvasId: 'edit_area_canvas',
								// 	x: 0,
								// 	y: 0,
								// 	width: canvasWidth,
								// 	height: canvasHeight,
								// 	success: function(res) {
								// 		console.log("打印3", res)
								// 		// command.setBitmap(60, 0, 1, res)
								// 	},
								// 	complete: function() {
								// 		console.log("打印4", command)
								// 		command.setPagePrint()
								// 		that.prepareSend(command.getData())
								// 	}
								// })
			},// 标签 宽60 高80 横向
			labelTest60X80(data) {
				console.log("打印1", data)
				let that = this;
				let canvasWidth = that.canvasWidth
				let canvasHeight = that.canvasHeight
				let command =blePlatform
				let {
					toLine,
					splitStr,
					notNullData
				} = that
				command.setSize(60, 83)
				command.setGap(0)
				command.setCls()
				command.setBackFeed(0)
				let row = 20
				let font = "TSS24.BF2"
				command.setTextRotation(toLine(12), row, font, 90, 1, 1, "物料编码:");
				command.setTextRotation(toLine(11), row, font, 90, 2, 2, notNullData(data.materialCode))
				command.setTextRotation(toLine(9), row, font, 90, 1, 1, "物料名称:")
				let materialName = notNullData(data.materialName)
				let materialNameList = splitStr(materialName, 20)
				let i = 0
				for (; i < materialNameList.length; i++) {
					command.setTextRotation(toLine(8 - i), row, font, 90, 1, 1, materialNameList[i])
				}
				command.setTextRotation(toLine(8 - i), row, font, 90, 1, 1, "供应商简称:" + notNullData(data
					.supplierName))
				command.setTextRotation(toLine(7 - i), row, font, 90, 1, 1, "供应商生产白期:" + notNullData(data.mfgTime))
				command.setTextRotation(toLine(7 - i), toLine(10), font, 90, 1, 1,"数量:")
				command.setTextRotation(toLine(7 - i), toLine(12), font, 90, 2, 2, notNullData(data.qty))
				command.setTextRotation(toLine(6 - i), row, font, 90, 1, 1, "采购单号:" + notNullData(data.po))
				command.setTextRotation(toLine(5 - i), row, font, 90, 1, 1, "供应商生产批次:" + + notNullData(data
					.supplierBatch))
				command.setQRRotation(toLine(12), toLine(12), "L", 5, "A", 90, notNullData(data.code))
				let codeStr = notNullData(data.code)
				let codeList = splitStr(codeStr, 11)
				for (let codeListIndex = 0; codeListIndex < codeList.length; codeListIndex++) {
					command.setTextRotation(toLine((9 - codeListIndex)), toLine(12), font, 90, 1, 1, codeList[
						codeListIndex])
				}


				console.log("打印2", command)
				uni.canvasGetImageData({
					canvasId: 'edit_area_canvas',
					x: 0,
					y: 0,
					width: canvasWidth,
					height: canvasHeight,
					success: function(res) {
						console.log("打印3", res)
						// command.setBitmap(60, 0, 1, res)
					},
					complete: function() {
						console.log("打印4", command)
						command.setPagePrint()
						that.prepareSend(command.getData())
					}
				})
				
				
			},
			// 获取非空数据
						notNullData(data) {
							return (data ? data : data == "0" ? 0 : "")
						},
						// 文本分割
									splitStr(str, len) {
										let reg = new RegExp(".{" + len + "}", 'g');
										let list = str.match(reg)
										if (list == null) {
											list = []
										}
										// 尾数添加
										list.push(str.substr(list.length * len, str.length % len))
										return list
									},
						toLine(index) {
										let that = this
										let lineHeight = 30
										if (that.templateType == '80*60') {
											lineHeight = 30
										} else if (that.templateType == '40*30') {
											lineHeight = 30 * 0.6
										} else if (that.templateType == '60*80') {
											lineHeight = 30 * 1.2
										} else if (that.templateType == '60*40') {
											lineHeight = 30 * 1
										}
										return lineHeight * index
									},
						
						
			senBleLabel2(){
				  console.log('票据模式')
				  console.log(" 连接状态 " + blePlatform.bluetoothGetState())
				  
				  blePlatform.clear()//新增方法 清除数据
				  blePlatform.setSize(40, 80)
				  blePlatform.setGap(0)
				  blePlatform.setCls()
				  blePlatform.setBackFeed(0)
				  let col = 0
				  let font = "TSS16.BF2"
				  blePlatform.setText(col,0, font, 1, 1, "物料编码\"\:测试")
				  blePlatform.setQR(col, 20, "L", 5, "A","1234567890ABCDEF")
				  
				
				var that = this;
				
				// 选择图片
				uni.chooseImage({
				  count: 1, // 默认9
				  sizeType: ['original', 'compressed'], // 可以指定是原图还是压缩图，默认二者都有
				  sourceType: ['album', 'camera'], // 可以指定来源是相册还是相机，默认二者都有
				  success: function (res) {
									   // 成功选择图片后，获取图片的临时文件路径
									    const tempFilePaths = res.tempFilePaths;
										// 成功获取图片的B图片信息
										var imgPath = tempFilePaths[0].substring(7)//实际路径
										 // 成功获取图片的Bitmap信息
										 console.log('图片路径:',imgPath );
										 
										 blePlatform.addBitmapPath(0,150,200,imgPath);
										 
										blePlatform.setPagePrint()
										let ret = blePlatform.startPrint();//新增方法 开始打印
										console.log("打印2", "startPrint()"+ret)
				
				},
				  fail: function (error) {
				    console.error('选择图片失败', error);
				  }
				});
				
			},
			senBleLabel3(){
							  console.log('票据模式')
						console.log(" 连接状态 " + blePlatform.bluetoothGetState())	  
							  
							  blePlatform.clear()//新增方法 清除数据
							  blePlatform.setSize(40, 80)
							  blePlatform.setGap(0)
							  blePlatform.setCls()
							  blePlatform.setBackFeed(0)
							  let col = 0
							  let font = "TSS16.BF2"
							  blePlatform.setText(col,0, font, 1, 1, "物料编码\"\:测试")
							  blePlatform.setQR(col, 20, "L", 5, "A","1234567890ABCDEF")
							  
							
							var that = this;
							
							
							
							uni.getImageInfo({
								src:'/static/sanjin.png',
								success: (res) => {
									const tempFilePaths = res.path;
									 console.log('图片路径:',tempFilePaths );
									 var imgPathTemp = tempFilePaths.substring(7)//实际路径
									 blePlatform.addBitmapPath(0,150,200,imgPathTemp);
									  
									 blePlatform.setPagePrint()
									 let ret = blePlatform.startPrint();//新增方法 开始打印
									 console.log("打印2", "startPrint()"+ret)
									 
								},
								fail: (res) => {
									 console.error('获取图片信息失败', error);
								}
							});
							
						},
		}
	}
</script>

<style>

</style>
